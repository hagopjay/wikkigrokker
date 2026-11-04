/* WikiData.x3d hyperbolic viewer.
 *
 * Geometry: the hyperboloid model of hyperbolic 3-space, coordinates (x, y, z, w) with the
 * metric diag(1, 1, 1, -1). Every class i has a local transform L[i] = Rz(az) Ry(lat) Bz(s*d)
 * from its parent (built by scripts/build_hyperball.py after Munzner's H3). Frames are
 * composed outward from whichever class is in focus, so precision is highest where you look.
 * Display is the Poincare ball: p = (x, y, z) / (1 + w).
 *
 * The X3D scene (scene.x3d) holds the nodes; this file only writes field values through
 * X_ITE's scene access interface.
 */
(async function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const FMT = new Intl.NumberFormat("en-US");
  const COMPACT = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
  const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SHELL_R = 1.22;
  const R0 = rotY(Math.PI / 2); // focus faces +x; its parent lies toward -x
  const HEAT = ["#383835", "#184f95", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef", "#cde2fb"];
  const HEAT_EDGES = [0, 0.05, 0.15, 0.3, 0.5, 0.75];

  function fail(msg) {
    const e = $("#err");
    e.style.display = "flex";
    e.textContent = msg;
    window.wgError = msg;
  }

  // X_ITE comes from the jsDelivr CDN (pinned version, integrity-checked); a local copy is the fallback
  if (typeof X3D === "undefined") {
    await new Promise((res) => { const sc = document.createElement("script"); sc.src = "x_ite/x_ite.min.js"; sc.onload = sc.onerror = res; document.head.appendChild(sc); });
  }
  if (typeof X3D === "undefined") { fail("The X3D viewer library (X_ITE) could not be loaded. Check the connection and reload."); return; }

  // ---------- data ----------
  let data, legend;
  try {
    [data, legend] = await Promise.all([
      fetch("data/viewer_data.json").then((r) => { if (!r.ok) throw new Error("viewer_data.json " + r.status); return r.json(); }),
      fetch("legend.json").then((r) => { if (!r.ok) throw new Error("legend.json " + r.status); return r.json(); }),
    ]);
  } catch (e) {
    fail("The class data could not be loaded (" + e.message + "). Reload the page to try again.");
    return;
  }
  const nd = data.nodes;
  const N = nd.label.length;
  const parent = Int32Array.from(nd.parent);
  const kids = Array.from({ length: N }, () => []);
  for (let i = 1; i < N; i++) kids[parent[i]].push(i);
  const legIdx = new Map(legend.map((l, i) => [l.label, i]));
  const group = new Int32Array(N);
  for (let i = 1; i < N; i++) group[i] = legIdx.get(nd.label[nd.branch[i]]);
  const hexRGB = (h) => [1, 3, 5].map((k) => parseInt(h.slice(k, k + 2), 16) / 255);
  const groupRGB = legend.map((l) => hexRGB(l.hex));
  const S = data.shell.length;
  const vit = new Float32Array(N * S);
  const vitTotal = new Float32Array(N);
  for (const [k, list] of Object.entries(data.vital)) {
    const i = +k;
    for (const [j, c] of list) { vit[i * S + j] = c; vitTotal[i] += c; }
  }
  const nmax = Math.max(...nd.n), vmax = Math.max(...nd.views);
  const sizeN = nd.n.map((x) => Math.min(1, Math.max(0.3, 0.3 + 0.7 * Math.log10(1 + x) / Math.log10(1 + nmax))));
  const cap = nd.rho.map((r, i) => r / sizeN[i]);
  const sizeV = nd.views.map((x) => Math.min(1, Math.max(0.3, 0.3 + 0.7 * Math.log10(1 + x) / Math.log10(1 + vmax))));
  const byQid = new Map();
  nd.qid.forEach((q, i) => { if (q) byQid.set(q, i); });
  const topicName = (j) => { const p = data.shell[j], t = data.meta.topics[p.topic]; return p.name === "General" ? t : t + ": " + p.name; };
  const flowsBySrc = new Map(), flowsByDst = new Map();
  for (const f of data.flows) {
    if (!flowsBySrc.has(f[0])) flowsBySrc.set(f[0], []);
    if (!flowsByDst.has(f[1])) flowsByDst.set(f[1], []);
    flowsBySrc.get(f[0]).push(f);
    flowsByDst.get(f[1]).push(f);
  }

  // ---------- Lorentz algebra (row-major 4x4) ----------
  function I4() { const m = new Float64Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m; }
  function mul(a, b) {
    const o = new Float64Array(16);
    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) {
      o[r * 4 + c] = a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c] + a[r * 4 + 2] * b[8 + c] + a[r * 4 + 3] * b[12 + c];
    }
    return o;
  }
  function rotZ(t) { const m = I4(), c = Math.cos(t), s = Math.sin(t); m[0] = c; m[1] = -s; m[4] = s; m[5] = c; return m; }
  function rotY(t) { const m = I4(), c = Math.cos(t), s = Math.sin(t); m[0] = c; m[2] = s; m[8] = -s; m[10] = c; return m; }
  function boostZ(d) { const m = I4(), ch = Math.cosh(d), sh = Math.sinh(d); m[10] = ch; m[11] = sh; m[14] = sh; m[15] = ch; return m; }
  function boostDir(n, b) {
    const m = I4(), ch = Math.cosh(b), sh = Math.sinh(b);
    for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) m[r * 4 + c] = (r === c ? 1 : 0) + (ch - 1) * n[r] * n[c];
    for (let r = 0; r < 3; r++) { m[r * 4 + 3] = sh * n[r]; m[12 + r] = sh * n[r]; }
    m[15] = ch;
    return m;
  }
  function linv(m) { // Lorentz inverse = eta m^T eta
    const o = new Float64Array(16);
    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) o[r * 4 + c] = m[c * 4 + r] * ((r === 3) !== (c === 3) ? -1 : 1);
    return o;
  }
  function quatFromM(m) {
    const [m00, m01, m02, m10, m11, m12, m20, m21, m22] = [m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]];
    const tr = m00 + m11 + m22;
    let s;
    if (tr > 0) { s = Math.sqrt(tr + 1) * 2; return [0.25 * s, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s]; }
    if (m00 > m11 && m00 > m22) { s = Math.sqrt(1 + m00 - m11 - m22) * 2; return [(m21 - m12) / s, 0.25 * s, (m01 + m10) / s, (m02 + m20) / s]; }
    if (m11 > m22) { s = Math.sqrt(1 + m11 - m00 - m22) * 2; return [(m02 - m20) / s, (m01 + m10) / s, 0.25 * s, (m12 + m21) / s]; }
    s = Math.sqrt(1 + m22 - m00 - m11) * 2; return [(m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, 0.25 * s];
  }
  function slerpFromId(q, t) {
    let [w, x, y, z] = q;
    if (w < 0) { w = -w; x = -x; y = -y; z = -z; }
    const th = Math.acos(Math.min(1, w));
    if (th < 1e-7) return [1, 0, 0, 0];
    const s = Math.sin(th), a = Math.sin((1 - t) * th) / s, b = Math.sin(t * th) / s;
    return [a + b * w, b * x, b * y, b * z];
  }
  function matFromQuat([w, x, y, z]) {
    const m = I4();
    m[0] = 1 - 2 * (y * y + z * z); m[1] = 2 * (x * y - z * w); m[2] = 2 * (x * z + y * w);
    m[4] = 2 * (x * y + z * w); m[5] = 1 - 2 * (x * x + z * z); m[6] = 2 * (y * z - x * w);
    m[8] = 2 * (x * z - y * w); m[9] = 2 * (y * z + x * w); m[10] = 1 - 2 * (x * x + y * y);
    return m;
  }
  function applyAll(M, Xin, Xout) {
    for (let i = 0; i < N; i++) {
      const o = i * 4, x = Xin[o], y = Xin[o + 1], z = Xin[o + 2], w = Xin[o + 3];
      Xout[o] = M[0] * x + M[1] * y + M[2] * z + M[3] * w;
      Xout[o + 1] = M[4] * x + M[5] * y + M[6] * z + M[7] * w;
      Xout[o + 2] = M[8] * x + M[9] * y + M[10] * z + M[11] * w;
      Xout[o + 3] = M[12] * x + M[13] * y + M[14] * z + M[15] * w;
    }
  }

  // ---------- state ----------
  const state = {
    focus: 0, history: [], sizeBy: "n", shell: false, alts: true, flows: false, labels: true,
    topic: -1, spread: data.meta.spread_default, hover: -1, hoverPatch: -1, touring: false, frozen: false,
  };
  let L = [], Linv = [];
  function buildLocal(s) {
    L = new Array(N); Linv = new Array(N);
    L[0] = I4(); Linv[0] = I4();
    for (let i = 1; i < N; i++) {
      L[i] = mul(mul(rotZ(nd.az[i]), rotY(nd.lat[i])), boostZ(s * nd.d[i]));
      Linv[i] = linv(L[i]);
    }
  }
  function framesFrom(f) {
    const F = new Array(N), seen = new Uint8Array(N), q = [f];
    F[f] = R0; seen[f] = 1;
    while (q.length) {
      const v = q.pop();
      for (const c of kids[v]) if (!seen[c]) { F[c] = mul(F[v], L[c]); seen[c] = 1; q.push(c); }
      const p = parent[v];
      if (v !== 0 && !seen[p]) { F[p] = mul(F[v], Linv[v]); seen[p] = 1; q.push(p); }
    }
    return F;
  }
  function columns(F) {
    const X = new Float64Array(N * 4);
    for (let i = 0; i < N; i++) { const m = F[i]; X[i * 4] = m[3]; X[i * 4 + 1] = m[7]; X[i * 4 + 2] = m[11]; X[i * 4 + 3] = m[15]; }
    return X;
  }

  // ---------- X3D ----------
  const canvas = $("#ball");
  // X_ITE keeps its view hidden until its own stylesheet loads. Where a page policy blocks that
  // stylesheet (the artifact host allows inline styles only), add the few layout rules it needs.
  if (canvas.shadowRoot) {
    const st = document.createElement("style");
    st.textContent = ".x_ite-private-browser{height:100%;outline:none;overflow:hidden;position:relative;user-select:none}" +
      ".x_ite-private-surface{height:100%}.x_ite-private-canvas{display:block;height:100%;width:100%}" +
      ".x_ite-private-splash-screen,.x_ite-private-buttons{display:none}";
    canvas.shadowRoot.appendChild(st);
    const inner = canvas.shadowRoot.querySelector(".x_ite-private-browser");
    if (inner) inner.style.display = "";
  }
  let browser, scene;
  try {
    browser = canvas.browser || X3D.getBrowser(canvas);
    browser.setBrowserOption("PrimitiveQuality", "LOW");
    browser.setBrowserOption("ContextMenu", false);
    browser.setBrowserOption("Notifications", false);
    browser.setBrowserOption("ContentScale", Math.min(window.devicePixelRatio || 1, 2));
    // the artifact host serves the scene as .xml; a local checkout has .x3d
    await browser.loadURL(new X3D.MFString("scene.xml", "scene.x3d"));
    scene = browser.currentScene;
  } catch (e) {
    fail("The 3D view could not start (" + (e && e.message ? e.message : e) + "). It needs a browser with WebGL 2.");
    return;
  }
  const node = (n) => { const x = scene.getNamedNode(n); if (!x) throw new Error("scene node " + n + " missing"); return x; };
  const instG = legend.map((_, g) => node("G" + g));
  const instH = HEAT.map((_, b) => node("H" + b));
  const edges = node("Edges"), edgeCoord = node("EdgesCoord"), edgeColor = node("EdgesColor");
  const alts = node("Alts"), altCoord = node("AltsCoord"), altColor = node("AltsColor");
  const flows = node("Flows"), flowCoord = node("FlowsCoord"), flowColor = node("FlowsColor");
  const shellSwitch = node("ShellSwitch"), shellColor = node("ShellColor");
  const ribbons = node("RibbonFaces"), ribCoord = node("RibbonCoord"), ribColor = node("RibbonColor");
  const focusMark = node("FocusMark"), hoverMark = node("HoverMark");
  const eye = node("Eye");
  const instHG = legend.map((_, g) => node("HG" + g)), instHH = HEAT.map((_, b) => node("HH" + b));
  const spin = node("Spin"), dotCoord = node("FlowDotsCoord"), dotColor = node("FlowDotsColor");
  const focusShader = node("FocusShader");
  const MF = (T, arr) => X3D[T].fromArray(Array.prototype.slice.call(arr));

  // static edge topology and colors
  const SEG = 4, PPE = SEG + 1;
  {
    const idx = [], col = new Float32Array((N - 1) * PPE * 3);
    for (let e = 0; e < N - 1; e++) {
      for (let k = 0; k < PPE; k++) idx.push(e * PPE + k);
      idx.push(-1);
      const rgb = groupRGB[group[e + 1]];
      for (let k = 0; k < PPE; k++) for (let c = 0; c < 3; c++) col[(e * PPE + k) * 3 + c] = rgb[c] * (0.66 - 0.3 * k / SEG);
    }
    edges.coordIndex = X3D.MFInt32.fromArray(idx);
    edgeColor.color = MF("MFColor", col);
  }
  const edgeBuf = new Float32Array((N - 1) * PPE * 3);

  // ---------- camera (from the ProximitySensor) ----------
  const cam = { pos: [0, 0, 3.15], right: [1, 0, 0], up: [0, 1, 0], fwd: [0, 0, -1], fov: 0.75 };
  function setOrientation(x, y, z, a) {
    const n = Math.hypot(x, y, z) || 1; x /= n; y /= n; z /= n;
    const c = Math.cos(a), s = Math.sin(a), t = 1 - c;
    const R = [t * x * x + c, t * x * y - s * z, t * x * z + s * y,
      t * x * y + s * z, t * y * y + c, t * y * z - s * x,
      t * x * z - s * y, t * y * z + s * x, t * z * z + c];
    cam.right = [R[0], R[3], R[6]]; cam.up = [R[1], R[4], R[7]]; cam.fwd = [-R[2], -R[5], -R[8]];
  }
  let ribbonQueued = false;
  const queueRibbons = () => { if (state.shell && !ribbonQueued) { ribbonQueued = true; requestAnimationFrame(() => { ribbonQueued = false; drawRibbons(); }); } };
  eye.addFieldCallback("app", "position_changed", (v) => { cam.pos = [v.x, v.y, v.z]; scheduleOverlay(); queueRibbons(); });
  eye.addFieldCallback("app", "orientation_changed", (r) => { setOrientation(r.x, r.y, r.z, r.angle); scheduleOverlay(); });

  // ---------- positions & rendering ----------
  buildLocal(state.spread);
  let Fcur = framesFrom(0);
  let Xcur = columns(Fcur);
  const P = new Float64Array(N * 3), E = new Float64Array(N);
  const SX = new Float32Array(N), SY = new Float32Array(N), SR = new Float32Array(N), SZ = new Float32Array(N);

  function euclid(rho, r2) { const t = Math.tanh(rho / 2); return t * (1 - r2) / (1 - r2 * t * t); }
  function rhoOf(i) { return state.spread * cap[i] * (state.sizeBy === "n" ? sizeN[i] : sizeV[i]); }

  function heatBin(i) {
    if (state.topic < 0) return -1;
    const tot = vitTotal[i];
    if (!tot) return 0;
    const sh = vit[i * S + state.topic] / tot;
    if (sh <= 0) return 0;
    let b = 1;
    for (let k = 1; k < HEAT_EDGES.length; k++) if (sh > HEAT_EDGES[k]) b = k + 1;
    return Math.min(b, HEAT.length - 1);
  }

  function geo(Xa, oa, Xb, ob, t, out, o) {
    const xa = Xa[oa], ya = Xa[oa + 1], za = Xa[oa + 2], wa = Xa[oa + 3];
    const xb = Xb[ob], yb = Xb[ob + 1], zb = Xb[ob + 2], wb = Xb[ob + 3];
    const ip = xa * xb + ya * yb + za * zb - wa * wb;
    const D = Math.acosh(Math.max(1, -ip));
    let x, y, z, w;
    if (D < 1e-9 || !isFinite(D)) { x = xa; y = ya; z = za; w = wa; }
    else {
      const s = Math.sinh(D), a = Math.sinh((1 - t) * D) / s, b = Math.sinh(t * D) / s;
      x = a * xa + b * xb; y = a * ya + b * yb; z = a * za + b * zb; w = a * wa + b * wb;
    }
    out[o] = x / (1 + w); out[o + 1] = y / (1 + w); out[o + 2] = z / (1 + w);
  }

  function render() {
    const X = Xcur;
    const counts = new Int32Array(legend.length + HEAT.length);
    const gOf = new Int32Array(N);
    for (let i = 0; i < N; i++) {
      const o = i * 4, w = 1 + X[o + 3];
      const px = X[o] / w, py = X[o + 1] / w, pz = X[o + 2] / w;
      P[i * 3] = px; P[i * 3 + 1] = py; P[i * 3 + 2] = pz;
      const e = euclid(rhoOf(i), Math.min(0.999999, px * px + py * py + pz * pz));
      E[i] = e;
      const hb = heatBin(i);
      gOf[i] = hb < 0 ? group[i] : legend.length + hb;
      if (e > 1.5e-4) counts[gOf[i]]++;
    }
    const T = Array.from(counts, (c) => new Float32Array(c * 3)), Sc = Array.from(counts, (c) => new Float32Array(c * 3)), fill = new Int32Array(counts.length);
    for (let i = 0; i < N; i++) {
      if (E[i] <= 1.5e-4) continue;
      const g = gOf[i], k = fill[g]++ * 3;
      T[g][k] = P[i * 3]; T[g][k + 1] = P[i * 3 + 1]; T[g][k + 2] = P[i * 3 + 2];
      Sc[g][k] = Sc[g][k + 1] = Sc[g][k + 2] = E[i];
    }
    const all = instG.concat(instH);
    all.forEach((inst, g) => {
      inst.visible = counts[g] > 0;
      inst.translations = MF("MFVec3f", T[g]);
      inst.scales = MF("MFVec3f", Sc[g]);
    });
    // glow halos: only for spheres big enough to read, so the rim does not haze over
    const hc = new Int32Array(counts.length);
    for (let i = 0; i < N; i++) if (E[i] > 0.003) hc[gOf[i]]++;
    const HT = Array.from(hc, (c) => new Float32Array(c * 3)), HS = Array.from(hc, (c) => new Float32Array(c * 3)), hf = new Int32Array(hc.length);
    for (let i = 0; i < N; i++) {
      if (E[i] <= 0.003) continue;
      const g = gOf[i], k = hf[g]++ * 3, sc = E[i] * (i === state.focus ? 2.9 : 2.3);
      HT[g][k] = P[i * 3]; HT[g][k + 1] = P[i * 3 + 1]; HT[g][k + 2] = P[i * 3 + 2];
      HS[g][k] = HS[g][k + 1] = HS[g][k + 2] = sc;
    }
    instHG.concat(instHH).forEach((inst, g) => {
      inst.visible = hc[g] > 0;
      inst.translations = MF("MFVec3f", HT[g]);
      inst.scales = MF("MFVec3f", HS[g]);
    });
    for (let i = 1; i < N; i++) {
      const p = parent[i];
      for (let k = 0; k < PPE; k++) geo(X, p * 4, X, i * 4, k / SEG, edgeBuf, ((i - 1) * PPE + k) * 3);
    }
    edgeCoord.point = MF("MFVec3f", edgeBuf);
    const f = state.focus;
    focusMark.translation = new X3D.SFVec3f(P[f * 3], P[f * 3 + 1], P[f * 3 + 2]);
    focusMark.scale = new X3D.SFVec3f(E[f], E[f], E[f]);
    drawHover();
    drawAlts();
    drawFlows();
    drawRibbons();
    scheduleOverlay();
  }

  function drawHover() {
    const h = state.hover;
    if (h < 0 || h === state.focus) { hoverMark.scale = new X3D.SFVec3f(0, 0, 0); return; }
    hoverMark.translation = new X3D.SFVec3f(P[h * 3], P[h * 3 + 1], P[h * 3 + 2]);
    hoverMark.scale = new X3D.SFVec3f(E[h], E[h], E[h]);
  }

  function arcSet(pairs, coordNode, colorNode, setNode, segs) {
    const pts = new Float32Array(pairs.length * (segs + 1) * 3), col = new Float32Array(pairs.length * (segs + 1) * 3), idx = [];
    arcSet.last = { pts, segs, n: pairs.length };
    pairs.forEach(([a, b, c0, c1], e) => {
      for (let k = 0; k <= segs; k++) {
        const o = (e * (segs + 1) + k) * 3, t = k / segs;
        geo(Xcur, a * 4, Xcur, b * 4, t, pts, o);
        for (let c = 0; c < 3; c++) col[o + c] = c0[c] + (c1[c] - c0[c]) * t;
        idx.push(e * (segs + 1) + k);
      }
      idx.push(-1);
    });
    setNode.coordIndex = X3D.MFInt32.fromArray(idx);
    coordNode.point = MF("MFVec3f", pts);
    colorNode.color = MF("MFColor", col);
  }

  function drawAlts() {
    const f = state.focus, list = state.alts ? (data.alts[f] || []) : [];
    const top = list.length ? list[0][1] : 1;
    arcSet(list.map(([b, n]) => { const k = 0.45 + 0.55 * n / top; return [f, b, [0.94 * k, 0.94 * k, 0.92 * k], [0.94 * k, 0.94 * k, 0.92 * k]]; }), altCoord, altColor, alts, 16);
  }

  function drawFlows() {
    if (!state.flows) { arcSet([], flowCoord, flowColor, flows, 12); return; }
    const f = state.focus, pairs = [];
    const mine = (flowsBySrc.get(f) || []).slice(0, 10).concat((flowsByDst.get(f) || []).slice(0, 10));
    const glob = data.flows.slice(0, 120);
    const dim = [0.16, 0.16, 0.15], mid = [0.54, 0.53, 0.5], hot = [0.98, 0.7, 0.1];
    const w = [];
    for (const [a, b, n] of glob) { pairs.push([a, b, dim, mid]); w.push([n, 0]); }
    for (const [a, b, n] of mine) { pairs.push([a, b, [0.35, 0.25, 0.05], hot]); w.push([n, 1]); }
    arcSet(pairs, flowCoord, flowColor, flows, 12);
    // particles: more dots on bigger flows, warm on the focus class's own flows
    const L0 = arcSet.last, top = Math.max(1, ...w.map((x) => x[0]));
    particles = [];
    w.forEach(([n, mineFlag], e) => {
      const k = 1 + Math.round(5 * Math.sqrt(n / top));
      for (let q = 0; q < k; q++) particles.push({ e, ph: (q + Math.random() * 0.5) / k, sp: 0.18 + 0.1 * Math.random(), c: mineFlag ? [1, 0.72, 0.2] : [0.75, 0.74, 0.7] });
    });
    flowPts = L0;
  }
  let particles = [], flowPts = null;
  function drawParticles(t) {
    if (!state.flows || !flowPts || !particles.length) { dotCoord.point = MF("MFVec3f", []); dotColor.color = MF("MFColor", []); return; }
    const { pts, segs } = flowPts, out = new Float32Array(particles.length * 3), col = new Float32Array(particles.length * 3);
    particles.forEach((p, k) => {
      const u = ((p.ph + t * p.sp) % 1) * segs, s0 = Math.floor(u), f = u - s0, b = (p.e * (segs + 1) + s0) * 3;
      for (let c = 0; c < 3; c++) { out[k * 3 + c] = pts[b + c] * (1 - f) + pts[b + 3 + c] * f; col[k * 3 + c] = p.c[c] * (0.4 + 0.6 * Math.sin(Math.PI * u / segs)); }
    });
    dotCoord.point = MF("MFVec3f", out);
    dotColor.color = MF("MFColor", col);
  }

  function shareColor(sh) {
    let b = 0;
    if (sh > 0) { b = 1; for (let k = 1; k < HEAT_EDGES.length; k++) if (sh > HEAT_EDGES[k]) b = k + 1; }
    return hexRGB(HEAT[Math.min(b, HEAT.length - 1)]);
  }

  function patchCenter(j, r) {
    const p = data.shell[j], az = (p.az0 + p.az1) / 2, la = (p.lat0 + p.lat1) / 2;
    return [r * Math.cos(la) * Math.cos(az), r * Math.sin(la), r * Math.cos(la) * Math.sin(az)];
  }

  function drawRibbons() {
    const f = state.focus, tot = vitTotal[f];
    const pc = [];
    for (let j = 0; j < S; j++) {
      const sh = tot ? vit[f * S + j] / tot : 0;
      const c = state.topic === j ? [0.94, 0.94, 0.92] : (tot && sh > 0 ? shareColor(sh) : hexRGB("#1a1a19"));
      pc.push(...c);
    }
    shellColor.color = X3D.MFColor.fromArray(pc);
    if (!state.shell || !tot) { ribbons.coordIndex = X3D.MFInt32.fromArray([]); ribCoord.point = MF("MFVec3f", []); ribColor.color = MF("MFColor", []); return; }
    const p0 = [P[f * 3], P[f * 3 + 1], P[f * 3 + 2]];
    const K = 14, pts = [], cols = [], idx = [], camD = Math.hypot(...cam.pos);
    let maxc = 0;
    for (let j = 0; j < S; j++) maxc = Math.max(maxc, vit[f * S + j]);
    for (let j = 0; j < S; j++) {
      const c = vit[f * S + j];
      if (!c) continue;
      const C = patchCenter(j, SHELL_R * 0.995), M = C.map((v, k) => v * 0.42 + p0[k] * 0.35);
      const Cw = [spinC * C[0] + spinS * C[2], C[1], -spinS * C[0] + spinC * C[2]];
      if (Math.hypot(Cw[0] - cam.pos[0], Cw[1] - cam.pos[1], Cw[2] - cam.pos[2]) < camD) continue; // patch faces away: skip its thread
      const w = 0.003 + 0.022 * c / maxc, col = shareColor(c / tot);
      const base = pts.length / 3;
      for (let s = 0; s <= K; s++) {
        const t = s / K, a = (1 - t) * (1 - t), b = 2 * (1 - t) * t, d = t * t;
        const Q = [0, 1, 2].map((k) => a * p0[k] + b * M[k] + d * C[k]);
        const T = [0, 1, 2].map((k) => 2 * (1 - t) * (M[k] - p0[k]) + 2 * t * (C[k] - M[k]));
        let side = [T[1] * Q[2] - T[2] * Q[1], T[2] * Q[0] - T[0] * Q[2], T[0] * Q[1] - T[1] * Q[0]];
        let sl = Math.hypot(...side);
        if (sl < 1e-6) { side = [T[1], -T[0], 0]; sl = Math.hypot(...side) || 1; }
        const hw = w * (0.25 + 0.75 * t) / 2 / sl;
        pts.push(Q[0] - side[0] * hw, Q[1] - side[1] * hw, Q[2] - side[2] * hw, Q[0] + side[0] * hw, Q[1] + side[1] * hw, Q[2] + side[2] * hw);
        const k = 0.35 + 0.65 * t;
        cols.push(col[0] * k, col[1] * k, col[2] * k, col[0] * k, col[1] * k, col[2] * k);
        if (s) idx.push(base + (s - 1) * 2, base + (s - 1) * 2 + 1, base + s * 2 + 1, base + s * 2, -1);
      }
    }
    ribbons.coordIndex = X3D.MFInt32.fromArray(idx);
    ribCoord.point = MF("MFVec3f", pts);
    ribColor.color = MF("MFColor", cols);
  }

  // ---------- overlay: labels, picking ----------
  const labelBox = $("#labels");
  const POOL = 44;
  const labs = Array.from({ length: POOL }, () => { const d = document.createElement("div"); d.className = "lab"; labelBox.appendChild(d); return d; });
  const topicLabs = Array.from({ length: data.meta.topics.length }, (_, t) => { const d = document.createElement("div"); d.className = "lab topic"; d.textContent = data.meta.topics[t]; labelBox.appendChild(d); return d; });
  let overlayQueued = false;
  function scheduleOverlay() { if (!overlayQueued) { overlayQueued = true; requestAnimationFrame(() => { overlayQueued = false; overlay(); }); } }
  function project(x0, y0, z0, W, H, f) {
    const x = spinC * x0 + spinS * z0, y = y0, z = -spinS * x0 + spinC * z0;
    const vx = x - cam.pos[0], vy = y - cam.pos[1], vz = z - cam.pos[2];
    const zc = vx * cam.fwd[0] + vy * cam.fwd[1] + vz * cam.fwd[2];
    if (zc < 0.02) return null;
    const xc = vx * cam.right[0] + vy * cam.right[1] + vz * cam.right[2];
    const yc = vx * cam.up[0] + vy * cam.up[1] + vz * cam.up[2];
    return [W / 2 + f * xc / zc, H / 2 - f * yc / zc, zc];
  }
  function overlay() {
    const W = canvas.clientWidth, H = canvas.clientHeight, f = (Math.min(W, H) / 2) / Math.tan(cam.fov / 2);
    for (let i = 0; i < N; i++) {
      const s = project(P[i * 3], P[i * 3 + 1], P[i * 3 + 2], W, H, f);
      if (!s) { SR[i] = -1; continue; }
      SX[i] = s[0]; SY[i] = s[1]; SZ[i] = s[2]; SR[i] = f * E[i] / s[2];
    }
    window.wgScreen = { SX, SY, SR, SZ, W, H };
    const placed = [];
    const camDist = Math.hypot(...cam.pos);
    let used = 0;
    if (state.labels) {
      const cand = [];
      for (let i = 0; i < N; i++) if (SR[i] > 5 || i === state.focus) cand.push(i);
      cand.sort((a, b) => (b === state.focus) - (a === state.focus) || SR[b] - SR[a]);
      for (const i of cand) {
        if (used >= POOL) break;
        const big = i === state.focus ? "focus" : SR[i] > 16 ? "big" : "";
        const txt = nd.label[i], wpx = txt.length * (big === "focus" ? 9.5 : big ? 8 : 6.6) + 6, hpx = big === "focus" ? 22 : big ? 18 : 15;
        const x = SX[i] + SR[i] + 4, y = SY[i] - hpx / 2;
        if (x > W || y < 0 || y > H) continue;
        const box = [x, y, x + wpx, y + hpx];
        if (placed.some((b) => !(box[2] < b[0] || box[0] > b[2] || box[3] < b[1] || box[1] > b[3]))) continue;
        placed.push(box);
        const d = labs[used++];
        d.textContent = txt;
        d.className = "lab " + big;
        d.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
        d.style.opacity = Math.max(0.35, Math.min(1, 1.25 - 0.32 * (SZ[i] - camDist + 1))).toFixed(2);
        d.style.display = "block";
      }
    }
    for (let k = used; k < POOL; k++) labs[k].style.display = "none";
    const dist0 = camDist;
    topicLabs.forEach((d, t) => {
      if (!state.shell) { d.style.display = "none"; return; }
      const js = data.shell.map((p, j) => [p, j]).filter(([p]) => p.topic === t);
      const az = (js[0][0].az0 + js[0][0].az1) / 2, la = Math.max(...js.map(([p]) => p.lat1)) + 0.07;
      const c0 = [SHELL_R * Math.cos(la) * Math.cos(az), SHELL_R * Math.sin(la), SHELL_R * Math.cos(la) * Math.sin(az)];
      const c = [spinC * c0[0] + spinS * c0[2], c0[1], -spinS * c0[0] + spinC * c0[2]];
      const farSide = Math.hypot(c[0] - cam.pos[0], c[1] - cam.pos[1], c[2] - cam.pos[2]) > dist0;
      const s = project(c0[0], c0[1], c0[2], W, H, f);
      if (!s || !farSide || s[1] < 112) { d.style.display = "none"; return; }
      d.style.display = "block";
      d.style.transform = `translate(${(s[0] - d.textContent.length * 3.4).toFixed(1)}px, ${(s[1] - 8).toFixed(1)}px)`;
    });
  }

  function pickNode(mx, my) {
    let best = -1, bz = Infinity;
    for (let i = 0; i < N; i++) {
      if (SR[i] < 0) continue;
      const r = Math.max(SR[i], 3.5), dx = mx - SX[i], dy = my - SY[i];
      if (dx * dx + dy * dy <= r * r && SZ[i] < bz) { bz = SZ[i]; best = i; }
    }
    return best;
  }
  function pickPatch(mx, my) {
    if (!state.shell) return -1;
    const W = canvas.clientWidth, H = canvas.clientHeight, f = (Math.min(W, H) / 2) / Math.tan(cam.fov / 2);
    const a = (mx - W / 2) / f, b = -(my - H / 2) / f;
    const d = [0, 1, 2].map((k) => cam.fwd[k] + cam.right[k] * a + cam.up[k] * b);
    const dl = Math.hypot(...d); d.forEach((v, k) => (d[k] = v / dl));
    const o = cam.pos, bb = o[0] * d[0] + o[1] * d[1] + o[2] * d[2], cc = o[0] ** 2 + o[1] ** 2 + o[2] ** 2 - SHELL_R ** 2;
    const disc = bb * bb - cc;
    if (disc < 0) return -1;
    const t = -bb + Math.sqrt(disc), pw = [o[0] + t * d[0], o[1] + t * d[1], o[2] + t * d[2]];
    const p = [spinC * pw[0] - spinS * pw[2], pw[1], spinS * pw[0] + spinC * pw[2]];
    const la = Math.asin(Math.max(-1, Math.min(1, p[1] / SHELL_R)));
    let az = Math.atan2(p[2], p[0]); if (az < 0) az += 2 * Math.PI;
    return data.shell.findIndex((q) => az >= q.az0 && az <= q.az1 && la >= q.lat0 && la <= q.lat1);
  }

  const tip = $("#tip");
  function showTip(mx, my, html) {
    tip.innerHTML = html; tip.style.display = "block";
    const W = canvas.clientWidth;
    tip.style.left = Math.min(mx + 14, W - 290) + "px"; tip.style.top = my + 14 + "px";
  }
  let down = null;
  canvas.addEventListener("pointerdown", (e) => { down = [e.offsetX, e.offsetY]; }, true);
  canvas.addEventListener("pointermove", (e) => {
    const mx = e.offsetX, my = e.offsetY;
    const h = pickNode(mx, my);
    if (h !== state.hover) { state.hover = h; drawHover(); }
    state.hoverPatch = h < 0 ? pickPatch(mx, my) : -1;
    if (h >= 0) showTip(mx, my, `<b>${esc(nd.label[h])}</b>${FMT.format(nd.n[h])} articles · ${COMPACT.format(nd.views[h])} reader arrivals`.replace(" · ", ", "));
    else if (state.hoverPatch >= 0) { const p = data.shell[state.hoverPatch]; showTip(mx, my, `<b>${esc(topicName(state.hoverPatch))}</b>${FMT.format(p.n)} Vital Articles. Click to light up the classes they belong to.`); }
    else tip.style.display = "none";
    canvas.style.cursor = h >= 0 || state.hoverPatch >= 0 ? "pointer" : "";
  }, true);
  canvas.addEventListener("pointerleave", () => { tip.style.display = "none"; state.hover = -1; drawHover(); });
  canvas.addEventListener("pointerup", (e) => {
    if (!down) return;
    const moved = Math.hypot(e.offsetX - down[0], e.offsetY - down[1]);
    down = null;
    if (moved > 5) return;
    stopTour();
    const h = pickNode(e.offsetX, e.offsetY);
    if (h >= 0) { focusOn(h); return; }
    const p = pickPatch(e.offsetX, e.offsetY);
    if (p >= 0) setTopic(state.topic === p ? -1 : p);
  }, true);

  // ---------- focus transitions ----------
  let anim = null;
  function focusOn(b, opts = {}) {
    if (b < 0 || b >= N) return;
    if (anim) { cancelAnimationFrame(anim.raf); Xcur = anim.Xnew; Fcur = anim.Fnew; state.focus = anim.to; anim = null; }
    if (b === state.focus) { showInfo(b); render(); return; }
    if (!opts.noHistory) state.history.push(state.focus);
    const a = state.focus, Fold = Fcur, Xold = Xcur;
    const Fnew = framesFrom(b), Xnew = columns(Fnew);
    const K = mul(R0, linv(Fold[b]));
    const xb = [Fold[b][3], Fold[b][7], Fold[b][11]], D = Math.acosh(Math.max(1, Fold[b][15]));
    const nl = Math.hypot(...xb) || 1, n = xb.map((v) => v / nl);
    const Q = mul(K, boostDir(n, D)), q = quatFromM(Q), Kinv = linv(K);
    state.focus = b;
    showInfo(b);
    setHash(b);
    const dur = REDUCED || opts.instant ? 0 : Math.min(1700, 650 + 260 * D);
    const t0 = performance.now(), Xt = new Float64Array(N * 4);
    anim = { to: b, Xnew, Fnew, raf: 0 };
    const step = (now) => {
      let t = dur ? Math.min(1, (now - t0) / dur) : 1;
      t = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      const A = mul(matFromQuat(slerpFromId(q, t)), boostDir(n, -t * D));
      if (t < 0.5) applyAll(A, Xold, Xt); else applyAll(mul(A, Kinv), Xnew, Xt);
      Xcur = Xt;
      render();
      if (t < 1) anim.raf = requestAnimationFrame(step);
      else { Xcur = Xnew; Fcur = Fnew; anim = null; render(); }
    };
    anim.raf = requestAnimationFrame(step);
    void a;
  }

  function setSpread(s) {
    state.spread = s;
    buildLocal(s);
    Fcur = framesFrom(state.focus);
    Xcur = columns(Fcur);
    render();
  }

  function setTopic(j) {
    state.topic = j;
    const h = $("#heat");
    if (j < 0) { h.style.display = "none"; $("#legend").style.display = ""; render(); showInfo(state.focus); return; }
    const p = data.shell[j];
    h.innerHTML = `<b>${esc(topicName(j))}</b><br>Each class is shaded by the share of its Vital Articles that sit in this topic.
      <div class="ramp">${HEAT.slice(1).map((c) => `<i style="background:${c}"></i>`).join("")}</div>
      <div class="ends"><span>under 5%</span><span>over 75%</span></div>
      <span style="color:var(--paper-ink-2)">Dark grey: no Vital Articles in the class.</span><br>
      <button class="lk" id="heat-clear">Show kinds again</button>`;
    h.style.display = "block";
    $("#legend").style.display = "none";
    $("#heat-clear").onclick = () => setTopic(-1);
    if (!state.shell) toggle("shell", true);
    render();
    showInfo(state.focus);
  }

  // ---------- infobox ----------
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const wiki = (t) => "https://en.wikipedia.org/wiki/" + encodeURIComponent(t.replace(/ /g, "_"));
  function path(i) { const p = []; while (i >= 0) { p.unshift(i); i = i === 0 ? -1 : parent[i]; } return p; }
  function kindLine(i) {
    const q = nd.qid[i];
    if (q) return `Wikidata class <a href="https://www.wikidata.org/wiki/${q}" target="_blank" rel="noopener">${q}</a>`;
    if (i === 0) return "Root of the scaffold (schema.org Thing)";
    return "Scaffold class from YAGO 4.5 and schema.org";
  }
  function classButton(j, extra = "") { return `<button class="lk" data-go="${j}">${esc(nd.label[j])}</button>${extra}`; }
  function showInfo(i) {
    const box = $("#box");
    const crumbs = path(i).map((j, k, arr) => k === arr.length - 1 ? `<span>${esc(nd.label[j])}</span>` : classButton(j)).join('<span class="sep">›</span>');
    const per = nd.n[i] ? nd.views[i] / nd.n[i] : 0;
    const rows = [
      ["Articles in this class and below", FMT.format(nd.n[i])],
      nd.own[i] !== nd.n[i] ? ["Placed directly here", FMT.format(nd.own[i])] : null,
      nd.nfold[i] ? ["Smaller subclasses merged in", FMT.format(nd.nfold[i])] : null,
      kids[i].length ? ["Subclasses shown", FMT.format(kids[i].length)] : null,
      ["Reader arrivals, October 2026", FMT.format(nd.views[i])],
      ["Arrivals per article", per >= 100 ? FMT.format(Math.round(per)) : per.toFixed(1)],
      ["Vital Articles", vitTotal[i] ? FMT.format(vitTotal[i]) : "none"],
    ].filter(Boolean);
    let html = `<div class="hd"><h1>${esc(nd.label[i])}</h1><div class="kind">${kindLine(i)}</div></div>
      <div id="crumbs">${crumbs}</div>
      <table class="facts">${rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("")}</table>`;
    const tops = nd.top[i] || [];
    html += `<h2>Most read</h2><p class="note">Articles with the most arrivals in the October 2026 clickstream. Sharp spikes, such as Hyphen-minus, are probably automated traffic.</p>`;
    html += tops.length ? `<ol>${tops.map(([t, v]) => `<li><a href="${wiki(t)}" target="_blank" rel="noopener">${esc(t)}</a><span class="num">${COMPACT.format(v)}</span></li>`).join("")}</ol>` : `<p class="empty">No reader data for this class.</p>`;
    if (vitTotal[i]) {
      const list = [];
      for (let j = 0; j < S; j++) if (vit[i * S + j]) list.push([j, vit[i * S + j]]);
      list.sort((a, b) => b[1] - a[1]);
      const mx = list[0][1];
      html += `<h2>Topics of its Vital Articles</h2><p class="note">${FMT.format(vitTotal[i])} of this class's articles are on Wikipedia's Vital Articles lists. Click a topic to shade every class by it.</p><div class="bars">` +
        list.slice(0, 8).map(([j, c]) => { const p = data.shell[j]; return `<button class="bar${state.topic === j ? " on" : ""}" data-topic="${j}"><span class="bl">${esc(topicName(j))}</span><span class="bt">${Math.round(100 * c / vitTotal[i])}%</span><span class="bf"><i style="width:${(100 * c / mx).toFixed(1)}%"></i></span></button>`; }).join("") + `</div>`;
    }
    const al = data.alts[i] || [];
    html += `<h2>Also a subclass of</h2>`;
    html += al.length ? `<p class="note">Wikidata gives this class (or classes folded into it) other parents too. The tree keeps one; these are the rest, shown as white arcs.</p><ul>${al.map(([b, n]) => `<li>${classButton(b)}<span class="num">${n} link${n > 1 ? "s" : ""}</span></li>`).join("")}</ul>` : `<p class="empty">No other parents outside its own branch.</p>`;
    const out = (flowsBySrc.get(i) || []).slice(0, 6), inn = (flowsByDst.get(i) || []).slice(0, 6);
    html += `<h2>Where readers go next</h2>`;
    html += out.length || inn.length ? `<div class="flowcols"><div><h3>From here to</h3><ul>${out.map(([, b, n]) => `<li>${classButton(b, `<span class="num">${COMPACT.format(n)}</span>`)}</li>`).join("") || "<li>none listed</li>"}</ul></div><div><h3>Arriving from</h3><ul>${inn.map(([a, , n]) => `<li>${classButton(a, `<span class="num">${COMPACT.format(n)}</span>`)}</li>`).join("") || "<li>none listed</li>"}</ul></div></div>` : `<p class="empty">Not among the 1,500 largest class-to-class flows.</p>`;
    const ch = kids[i].slice().sort((a, b) => nd.n[b] - nd.n[a]).slice(0, 12);
    if (ch.length) html += `<h2>Largest subclasses</h2><ul>${ch.map((j) => `<li>${classButton(j, `<span class="num">${FMT.format(nd.n[j])}</span>`)}</li>`).join("")}</ul>`;
    box.innerHTML = html;
    box.scrollTop = 0;
    box.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => { stopTour(); focusOn(+b.dataset.go); }));
    box.querySelectorAll("[data-topic]").forEach((b) => (b.onclick = () => setTopic(state.topic === +b.dataset.topic ? -1 : +b.dataset.topic)));
  }

  // ---------- controls ----------
  function toggle(key, val) {
    state[key] = val === undefined ? !state[key] : val;
    $("#t-" + key).setAttribute("aria-pressed", String(state[key]));
    if (key === "shell") shellSwitch.whichChoice = state.shell ? 0 : -1;
    render();
    if (key === "flows" && !state.flows) drawParticles(0);
  }
  ["shell", "alts", "flows", "labels"].forEach((k) => ($("#t-" + k).onclick = () => toggle(k)));
  const setSize = (m) => { state.sizeBy = m; $("#sz-n").setAttribute("aria-pressed", m === "n"); $("#sz-v").setAttribute("aria-pressed", m === "v"); render(); };
  $("#sz-n").onclick = () => setSize("n");
  $("#sz-v").onclick = () => setSize("v");
  const spreadEl = $("#spread");
  spreadEl.value = state.spread;
  spreadEl.oninput = () => setSpread(+spreadEl.value);
  $("#b-home").onclick = () => { stopTour(); focusOn(0); };
  $("#b-up").onclick = () => { stopTour(); if (state.focus) focusOn(parent[state.focus]); };
  $("#b-back").onclick = () => { stopTour(); if (state.history.length) focusOn(state.history.pop(), { noHistory: true }); };
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT") return;
    if (e.key === "Escape" && state.focus) focusOn(parent[state.focus]);
    if (e.key === "Backspace" && state.history.length) { e.preventDefault(); focusOn(state.history.pop(), { noHistory: true }); }
  });

  // search
  const qEl = $("#q"), hits = $("#hits");
  const lower = nd.label.map((s) => s.toLowerCase());
  let hitList = [], hitSel = 0;
  function runSearch() {
    const q = qEl.value.trim().toLowerCase();
    if (!q) { hits.style.display = "none"; return; }
    const exact = [], starts = [], has = [];
    for (let i = 0; i < N; i++) {
      const s = lower[i];
      if (s === q || nd.qid[i].toLowerCase() === q) exact.push(i); else if (s.startsWith(q)) starts.push(i); else if (s.includes(q)) has.push(i);
    }
    const byN = (a, b) => nd.n[b] - nd.n[a];
    hitList = exact.sort(byN).concat(starts.sort(byN), has.sort(byN)).slice(0, 12);
    hitSel = 0;
    hits.innerHTML = hitList.length ? hitList.map((i, k) => `<button role="option" data-i="${i}" class="${k === 0 ? "on" : ""}">${esc(nd.label[i])}<span>${FMT.format(nd.n[i])}</span></button>`).join("") : `<button disabled>No class with that name is shown</button>`;
    hits.style.display = "block";
    hits.querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => pick(+b.dataset.i)));
  }
  function pick(i) { hits.style.display = "none"; qEl.value = ""; stopTour(); focusOn(i); }
  qEl.addEventListener("input", runSearch);
  qEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && hitList.length) pick(hitList[hitSel]);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      hitSel = (hitSel + (e.key === "ArrowDown" ? 1 : hitList.length - 1)) % Math.max(1, hitList.length);
      hits.querySelectorAll("[data-i]").forEach((b, k) => b.classList.toggle("on", k === hitSel));
    }
    if (e.key === "Escape") hits.style.display = "none";
  });
  document.addEventListener("click", (e) => { if (!$("#search").contains(e.target)) hits.style.display = "none"; });

  // legend
  const tops = kids[0].slice().sort((a, b) => nd.n[b] - nd.n[a]);
  $("#legend").innerHTML = tops.map((i) => `<button data-go="${i}"><i style="background:${legend[group[i]].hex}"></i>${esc(nd.label[i])}</button>`).join("");
  $("#legend").querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => { stopTour(); focusOn(+b.dataset.go); }));

  // tour of the largest kinds
  let tourTimer = null;
  function stopTour() { if (tourTimer) { clearInterval(tourTimer); tourTimer = null; $("#b-tour").setAttribute("aria-pressed", "false"); $("#b-tour").textContent = "Tour"; } }
  $("#b-tour").onclick = () => {
    if (tourTimer) { stopTour(); return; }
    const stops = [0].concat(tops.slice(0, 9));
    let k = Math.max(0, stops.indexOf(state.focus));
    $("#b-tour").setAttribute("aria-pressed", "true"); $("#b-tour").textContent = "Stop tour";
    const next = () => { k = (k + 1) % stops.length; focusOn(stops[k]); };
    next();
    tourTimer = setInterval(next, 5200);
  };

  // hash routing: #Q5 or #c123
  function idFor(i) { return nd.qid[i] || "c" + i; }
  function setHash(i) { const h = "#" + idFor(i); if (location.hash !== h) { try { history.replaceState(null, "", h); } catch (e) { /* hash not writable here */ } } }
  function fromHash() {
    const h = decodeURIComponent(location.hash.slice(1));
    if (!h) return -1;
    if (byQid.has(h)) return byQid.get(h);
    const m = /^c(\d+)$/.exec(h);
    return m && +m[1] < N ? +m[1] : -1;
  }
  window.addEventListener("hashchange", () => { const i = fromHash(); if (i >= 0 && i !== state.focus) focusOn(i); });
  window.addEventListener("resize", scheduleOverlay);

  // footnote
  const m = data.meta;
  $("#foot").textContent = `${FMT.format(m.articles_typed)} of ${FMT.format(m.articles_total)} English Wikipedia articles with a Wikidata item have a type and appear here, in ${FMT.format(m.classes_shown)} classes (${FMT.format(m.classes_total)} before folding). Sources: Wikidata via QLever, Wikipedia dumps of 1 Oct 2026, clickstream for October 2026, Vital Articles lists.`;
  $("#subtitle").textContent = `${FMT.format(m.articles_typed)} articles, arranged by what each one is`;

  // ---------- idle spin, focus pulse, flow particles ----------
  let spinA = 0, spinC = 1, spinS = 0, lastInput = performance.now(), lastTick = performance.now();
  const touch = () => { lastInput = performance.now(); };
  ["pointerdown", "wheel", "keydown"].forEach((ev) => window.addEventListener(ev, touch, true));
  function tick(now) {
    const dt = Math.min(0.1, (now - lastTick) / 1000);
    lastTick = now;
    if (state.frozen) { requestAnimationFrame(tick); return; }
    if (!REDUCED && !anim && now - lastInput > 7000 && !state.touring) {
      spinA += dt * 0.045 * Math.min(1, (now - lastInput - 7000) / 3000);
      spinC = Math.cos(spinA); spinS = Math.sin(spinA);
      spin.rotation = new X3D.SFRotation(0, 1, 0, spinA);
      scheduleOverlay();
      queueRibbons();
    }
    if (!REDUCED) focusShader.strength = 0.75 + 0.25 * Math.sin(now / 520);
    if (state.flows) drawParticles(REDUCED ? 0 : now / 1000);
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // ---------- start: the ball unfolds from its root ----------
  showInfo(0);
  const start = fromHash();
  const target = state.spread;
  if (REDUCED) { render(); }
  else {
    const t0 = performance.now(), dur = 1500;
    const grow = (now) => {
      const t = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - t, 3);
      setSpread(0.04 + (target - 0.04) * e);
      if (t < 1) requestAnimationFrame(grow);
      else { spreadEl.value = target; if (start > 0) focusOn(start); window.wgReady = true; }
    };
    requestAnimationFrame(grow);
  }
  if (REDUCED) { if (start > 0) focusOn(start, { instant: true }); window.wgReady = true; }

  // hooks for the witnesses (tools/witness_eyes.mjs)
  window.wg = { state, focusOn: (i) => focusOn(i, { instant: true }), setTopic, toggle, setSize, N, labels: nd.label, n: nd.n, views: nd.views, P, E, project: () => window.wgScreen, cam, legend, group };
})();
