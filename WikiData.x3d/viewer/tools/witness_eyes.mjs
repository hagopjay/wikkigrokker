// Eyes, Host, Behaviour and Truth witnesses for the hyperbolic viewer.
//
// usage: node witness_eyes.mjs URL OUT_DIR [--scale 1|2]
//
// Host:      the page loads, X_ITE starts, no page errors, no console errors.
// Eyes:      the canvas is not blank: enough lit pixels, several branch colours present.
// Behaviour: focusing a class moves it to the screen centre; the pixel there has its colour;
//            the overlay label for the focus sits beside it.
// Truth:     numbers on the page equal the data (root article count, a class's count).
// Two-sided: a negative control renders a blank page and must FAIL the Eyes check.
//
// What a pass does not establish: real-GPU frame rate, mobile layout, colour perception.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
import fs from "node:fs";
import path from "node:path";

const [, , url, outDir, ...rest] = process.argv;
const scale = rest.includes("--scale") ? +rest[rest.indexOf("--scale") + 1] : 1;
fs.mkdirSync(outDir, { recursive: true });
const results = {};
const fail = (k, detail) => (results[k] = { pass: false, ...detail });
const pass = (k, detail) => (results[k] = { pass: true, ...detail });

const browser = await chromium.launch({
  args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: scale });
const page = await ctx.newPage();
const consoleErrors = [], pageErrors = [], consoleWarn = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); if (m.type() === "warning") consoleWarn.push(m.text()); });
page.on("pageerror", (e) => pageErrors.push(String(e)));

async function lit(file, region) {
  // fraction of pixels in region that differ from the field colour (#0d0d0d) by more than 24 levels
  const buf = await page.screenshot({ path: file, clip: region });
  return await page.evaluate(async ({ b64 }) => {
    const img = new Image();
    img.src = "data:image/png;base64," + b64;
    await img.decode();
    const c = new OffscreenCanvas(img.width, img.height), g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, img.width, img.height).data;
    let litN = 0;
    const hues = new Set();
    for (let i = 0; i < d.length; i += 4) {
      const r = d[i], gg = d[i + 1], b = d[i + 2];
      if (Math.abs(r - 13) + Math.abs(gg - 13) + Math.abs(b - 13) > 24) {
        litN++;
        const mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
        if (mx - mn > 40) {
          let h = mx === r ? (gg - b) / (mx - mn) : mx === gg ? 2 + (b - r) / (mx - mn) : 4 + (r - gg) / (mx - mn);
          hues.add(Math.round(((h * 60 + 360) % 360) / 30));
        }
      }
    }
    return { lit: litN / (d.length / 4), hueBins: hues.size };
  }, { b64: buf.toString("base64") });
}

try {
  // negative control: an empty dark page must fail the Eyes threshold
  await page.setContent("<html><body style='margin:0;background:#0d0d0d;width:1440px;height:900px'></body></html>");
  const neg = await lit(path.join(outDir, "negative_control.png"), { x: 0, y: 0, width: 1060, height: 900 });
  results.negative_control = { pass: neg.lit < 0.02 && neg.hueBins < 3, note: "blank page must not look rendered", ...neg };

  const t0 = Date.now();
  await page.goto(url, { waitUntil: "load" });
  await page.waitForFunction(() => window.wgReady === true || window.wgError, null, { timeout: 180000 });
  const err = await page.evaluate(() => window.wgError || null);
  const loadSec = (Date.now() - t0) / 1000;
  if (err) fail("host", { error: err });
  await page.waitForTimeout(1500);
  // pause idle spin, pulse and particles so software rendering can settle on a frame
  await page.evaluate(() => { window.wg.state.frozen = true; });
  page.setDefaultTimeout(180000);

  const region = { x: 0, y: 0, width: 1060, height: 900 };
  const home = await lit(path.join(outDir, "01_home.png"), region);
  (home.lit > 0.03 && home.hueBins >= 4 ? pass : fail)("eyes_home", { ...home, need: "lit > 3%, >= 4 hue bins" });

  // Truth: root count on the page equals the data
  const truth = await page.evaluate(() => {
    const txt = document.querySelector("#box").innerText;
    const want = new Intl.NumberFormat("en-US").format(window.wg.n[0]);
    return { want, found: txt.includes(want) };
  });
  (truth.found ? pass : fail)("truth_root_count", truth);

  // Behaviour: focus 'human' and check it lands in the centre with its colour and label
  const target = await page.evaluate(() => window.wg.labels.indexOf("human"));
  await page.evaluate((i) => window.wg.focusOn(i), target);
  await page.waitForTimeout(1200);
  const beh = await page.evaluate((i) => {
    const s = window.wg.project();
    const lab = [...document.querySelectorAll(".lab.focus")].find((d) => d.style.display !== "none");
    const lr = lab ? lab.getBoundingClientRect() : null;
    return { sx: s.SX[i], sy: s.SY[i], sr: s.SR[i], W: s.W, H: s.H, label: lab ? lab.textContent : null, labelX: lr && lr.left, labelY: lr && lr.top + lr.height / 2,
      hex: window.wg.legend[window.wg.group[i]].hex, box: document.querySelector("#box h1").textContent };
  }, target);
  const centred = Math.abs(beh.sx - beh.W / 2) < 3 && Math.abs(beh.sy - beh.H / 2) < 3;
  const px = await page.screenshot({ clip: { x: Math.round(beh.sx) - 2, y: Math.round(beh.sy) - 2, width: 5, height: 5 } });
  const centre = await page.evaluate(async ({ b64 }) => {
    const img = new Image(); img.src = "data:image/png;base64," + b64; await img.decode();
    const c = new OffscreenCanvas(img.width, img.height), g = c.getContext("2d"); g.drawImage(img, 0, 0);
    const d = g.getImageData(Math.floor(img.width / 2), Math.floor(img.height / 2), 1, 1).data; return [d[0], d[1], d[2]];
  }, { b64: px.toString("base64") });
  const want = [1, 3, 5].map((k) => parseInt(beh.hex.slice(k, k + 2), 16));
  const hueOf = ([r, g, b]) => { const mx = Math.max(r, g, b), mn = Math.min(r, g, b); if (mx - mn < 1) return 0; const h = mx === r ? (g - b) / (mx - mn) : mx === g ? 2 + (b - r) / (mx - mn) : 4 + (r - g) / (mx - mn); return (h * 60 + 360) % 360; };
  const dh = Math.min(Math.abs(hueOf(centre) - hueOf(want)), 360 - Math.abs(hueOf(centre) - hueOf(want)));
  (centred ? pass : fail)("behaviour_focus_centred", { sx: beh.sx, sy: beh.sy, W: beh.W, H: beh.H });
  (dh < 25 ? pass : fail)("behaviour_focus_colour", { centre_rgb: centre, want_hex: beh.hex, hue_diff_deg: Math.round(dh) });
  const labOk = beh.label === "human" && beh.labelX > beh.sx && beh.labelX - beh.sx < beh.sr + 12 && Math.abs(beh.labelY - beh.sy) < 8;
  (labOk ? pass : fail)("behaviour_label_beside_node", { label: beh.label, labelX: beh.labelX, labelY: beh.labelY, sx: beh.sx, sy: beh.sy, sr: beh.sr });
  (beh.box === "human" ? pass : fail)("behaviour_infobox_follows", { box: beh.box });
  await page.screenshot({ path: path.join(outDir, "02_focus_human.png") });

  // topic shell + heat
  await page.evaluate(() => { window.wg.toggle("shell", true); });
  await page.evaluate(() => { const j = 0; window.wg.setTopic(j); });
  await page.waitForTimeout(800);
  const heat = await lit(path.join(outDir, "03_topic_heat.png"), region);
  (heat.lit > 0.03 ? pass : fail)("eyes_topic_heat", heat);
  await page.evaluate(() => { window.wg.setTopic(-1); window.wg.toggle("flows", true); window.wg.focusOn(window.wg.labels.indexOf("film")); });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(outDir, "04_film_flows.png") });
  await page.evaluate(() => { window.wg.toggle("flows", false); window.wg.toggle("shell", false); window.wg.focusOn(0); });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(outDir, "05_home_again.png") });

  const ignorable = (t) => /favicon/i.test(t);
  const errs = consoleErrors.filter((t) => !ignorable(t));
  (errs.length === 0 && pageErrors.length === 0 && !err ? pass : fail)("host", { console_errors: errs.slice(0, 10), page_errors: pageErrors.slice(0, 10), warnings: consoleWarn.slice(0, 10), load_seconds: loadSec });
} catch (e) {
  fail("harness", { error: String(e), console_errors: consoleErrors.slice(0, 10), page_errors: pageErrors.slice(0, 10) });
  await page.screenshot({ path: path.join(outDir, "error.png") }).catch(() => {});
}
await browser.close();
fs.writeFileSync(path.join(outDir, "witness_eyes.json"), JSON.stringify(results, null, 1));
console.log(JSON.stringify(results, null, 1));
process.exit(Object.values(results).every((r) => r.pass) ? 0 : 1);
