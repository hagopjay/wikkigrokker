#!/usr/bin/env python3
"""Write the two X3D scenes for the hyperbolic viewer.

usage: write_x3d.py VIEWER_DATA.json BRANCH_COLORS.json OUT_DIR

  scene.x3d             live scene for the web viewer (X_ITE). Nodes are InstancedShape groups,
                        edges/arcs/ribbons are empty line and face sets; app.js fills them through
                        the scene access interface on every frame.
  hyperball_static.x3d  self-contained X3D 4.0 scene for desktop viewers: the root in focus,
                        every visible class as a sphere, geodesic edges, labels, the topic shell
                        and one viewpoint per top-level branch.
"""
import json
import math
import os
import sys
from xml.sax.saxutils import escape, quoteattr

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "loom"))
from loomlog import log  # noqa: E402

SHELL_R = 1.22
HEAT = ["#383835", "#184f95", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef", "#cde2fb"]  # bin 0 = no Vital articles
BG = "#0d0d0d"


def rgb(h, k=1.0):
    h = h.lstrip("#")
    return " ".join(f"{k * int(h[i:i + 2], 16) / 255:.4f}" for i in (0, 2, 4))


def head(title, components):
    comps = "\n".join(f"  <component name='{c}' level='{lv}'/>" for c, lv in components)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE X3D PUBLIC "ISO//Web3D//DTD X3D 4.0//EN" "https://www.web3d.org/specifications/x3d-4.0.dtd">
<X3D profile='Interactive' version='4.0' xmlns:xsd='http://www.w3.org/2001/XMLSchema-instance' xsd:noNamespaceSchemaLocation='https://www.web3d.org/specifications/x3d-4.0.xsd'>
 <head>
{comps}
  <meta name='title' content={quoteattr(title)}/>
  <meta name='description' content='English Wikipedia articles by Wikidata type (P31/P279 under a YAGO 4.5 scaffold), laid out in hyperbolic 3-space after Munzner H3, with a shell of Vital Articles topics.'/>
  <meta name='generator' content='WikiData.x3d scripts/write_x3d.py'/>
  <meta name='license' content='Data: Wikidata CC0; Wikipedia titles and clickstream CC BY-SA / CC0 per Wikimedia terms'/>
 </head>
 <Scene>
"""


def frames_root(n, spread):
    N = len(n["label"])
    R0 = rot_y(math.pi / 2)
    F = [None] * N
    for i in range(N):
        F[i] = R0 if i == 0 else F[n["parent"][i]] @ rot_z(n["az"][i]) @ rot_y(n["lat"][i]) @ boost_z(spread * n["d"][i])
    return F


def rot_z(a):
    c, s = math.cos(a), math.sin(a); m = np.eye(4); m[0, 0] = c; m[0, 1] = -s; m[1, 0] = s; m[1, 1] = c; return m


def rot_y(a):
    c, s = math.cos(a), math.sin(a); m = np.eye(4); m[0, 0] = c; m[0, 2] = s; m[2, 0] = -s; m[2, 2] = c; return m


def boost_z(d):
    m = np.eye(4); m[2, 2] = m[3, 3] = math.cosh(d); m[2, 3] = m[3, 2] = math.sinh(d); return m


def ball(X):
    return X[:3] / (1 + X[3])


def euclid_radius(rho, r2):
    t = math.tanh(rho / 2)
    return t * (1 - r2) / (1 - r2 * t * t)


def geodesic(Xa, Xb, k):
    ip = Xa[0] * Xb[0] + Xa[1] * Xb[1] + Xa[2] * Xb[2] - Xa[3] * Xb[3]
    D = math.acosh(max(1.0, -ip))
    pts = []
    for s in range(k + 1):
        t = s / k
        if D < 1e-9:
            X = Xa
        else:
            X = (math.sinh((1 - t) * D) * Xa + math.sinh(t * D) * Xb) / math.sinh(D)
        pts.append(ball(X))
    return pts


def shell_mesh(shell, inward):
    pts, idx, cidx = [], [], []
    inset = 0.006
    for j, p in enumerate(shell):
        a0, a1, l0, l1 = p["az0"] + inset, p["az1"] - inset, p["lat0"] + inset, p["lat1"] - inset
        na = max(2, math.ceil((a1 - a0) / 0.06))
        nl = max(2, math.ceil((l1 - l0) / 0.06))
        base = len(pts)
        for u in range(nl + 1):
            la = l0 + (l1 - l0) * u / nl
            for v in range(na + 1):
                az = a0 + (a1 - a0) * v / na
                pts.append((SHELL_R * math.cos(la) * math.cos(az), SHELL_R * math.sin(la), SHELL_R * math.cos(la) * math.sin(az)))
        for u in range(nl):
            for v in range(na):
                a = base + u * (na + 1) + v
                quad = [a, a + 1, a + na + 2, a + na + 1]
                P = [np.array(pts[q]) for q in quad[:3]]
                nrm = np.cross(P[1] - P[0], P[2] - P[0])
                outward = float(np.dot(nrm, P[0])) > 0
                if outward == inward:
                    quad = quad[::-1]
                idx += quad + [-1]
                cidx.append(j)
    return pts, idx, cidx


def look_at(pos, target):
    f = np.array(target) - np.array(pos)
    f /= np.linalg.norm(f)
    up = np.array([0.0, 1.0, 0.0])
    if abs(f @ up) > 0.98:
        up = np.array([0.0, 0.0, 1.0])
    x = np.cross(f, up); x /= np.linalg.norm(x)
    y = np.cross(x, f)
    M = np.column_stack([x, y, -f])  # camera axes in world coords
    ang = math.acos(max(-1.0, min(1.0, (np.trace(M) - 1) / 2)))
    if ang < 1e-9:
        return "0 0 1 0"
    ax = np.array([M[2, 1] - M[1, 2], M[0, 2] - M[2, 0], M[1, 0] - M[0, 1]])
    if np.linalg.norm(ax) < 1e-9:  # 180 degrees
        w, V = np.linalg.eigh(M)
        ax = V[:, np.argmax(w)]
    ax /= np.linalg.norm(ax)
    return f"{ax[0]:.5f} {ax[1]:.5f} {ax[2]:.5f} {ang:.5f}"


def fmt(v):
    return " ".join(f"{x:.5f}" for x in v)


HALO_VS = """data:x-shader/x-vertex,#version 300 es
precision highp float;
uniform mat4 x3d_ProjectionMatrix;
uniform mat4 x3d_ModelViewMatrix;
in vec4 x3d_Vertex;
in vec3 x3d_Normal;
in mat4 x3d_InstanceMatrix;
out vec3 vN;
out vec3 vV;
void main () {
  vec4 p = x3d_ModelViewMatrix * (x3d_InstanceMatrix * x3d_Vertex);
  vN = mat3 (x3d_ModelViewMatrix) * mat3 (x3d_InstanceMatrix) * x3d_Normal;
  vV = -p.xyz;
  gl_Position = x3d_ProjectionMatrix * p;
}
"""
RIM_VS = """data:x-shader/x-vertex,#version 300 es
precision highp float;
uniform mat4 x3d_ProjectionMatrix;
uniform mat4 x3d_ModelViewMatrix;
in vec4 x3d_Vertex;
in vec3 x3d_Normal;
out vec3 vN;
out vec3 vV;
void main () {
  vec4 p = x3d_ModelViewMatrix * x3d_Vertex;
  vN = mat3 (x3d_ModelViewMatrix) * x3d_Normal;
  vV = -p.xyz;
  gl_Position = x3d_ProjectionMatrix * p;
}
"""
# soft glow: bright in the middle, fading to the silhouette; dimmed with distance like the fog
GLOW_FS = """data:x-shader/x-fragment,#version 300 es
precision highp float;
uniform vec3 glowColor;
uniform float strength;
uniform float power;
uniform float fogRange;
in vec3 vN;
in vec3 vV;
out vec4 x3d_FragColor;
void main () {
  float f = abs (dot (normalize (vN), normalize (vV)));
  float fog = clamp (1.0 - length (vV) / fogRange, 0.0, 1.0);
  float a = strength * pow (f, power) * fog;
  x3d_FragColor = vec4 (glowColor * a, a);
}
"""
# rim light: bright at the silhouette, like an atmosphere
RIM_FS = """data:x-shader/x-fragment,#version 300 es
precision highp float;
uniform vec3 glowColor;
uniform float strength;
uniform float power;
in vec3 vN;
in vec3 vV;
out vec4 x3d_FragColor;
void main () {
  float f = 1.0 - abs (dot (normalize (vN), normalize (vV)));
  float a = strength * pow (f, power);
  x3d_FragColor = vec4 (glowColor * a, a);
}
"""
ADDITIVE = ("<BlendMode sourceColorFactor='ONE' destinationColorFactor='ONE' sourceAlphaFactor='ONE' destinationAlphaFactor='ONE'/>"
            "<DepthMode depthMask='false'/>")
FOG_RANGE = 10.0


def shader(def_name, vs_def, fs_def, vs_src, fs_src, color, strength, power, first_vs, first_fs, fog=True):
    vs = f"<ShaderPart DEF='{vs_def}' type='VERTEX'><![CDATA[{vs_src}]]></ShaderPart>" if first_vs else f"<ShaderPart USE='{vs_def}'/>"
    fs = f"<ShaderPart DEF='{fs_def}' type='FRAGMENT'><![CDATA[{fs_src}]]></ShaderPart>" if first_fs else f"<ShaderPart USE='{fs_def}'/>"
    fields = (f"<field accessType='inputOutput' type='SFColor' name='glowColor' value='{color}'/>"
              f"<field accessType='inputOutput' type='SFFloat' name='strength' value='{strength}'/>"
              f"<field accessType='inputOutput' type='SFFloat' name='power' value='{power}'/>")
    if fog:
        fields += f"<field accessType='inputOutput' type='SFFloat' name='fogRange' value='{FOG_RANGE}'/>"
    return f"<ComposedShader DEF='{def_name}' language='GLSL'>{fields}{vs}{fs}</ComposedShader>"


def write_live(d, colors, out):
    legend = colors["legend"]
    s = [head("WikiData.x3d: Wikipedia type tree in hyperbolic space (live)",
              [("EnvironmentalEffects", 3), ("Shaders", 1), ("X_ITE", 1)])]
    s.append(" <WorldInfo title='WikiData.x3d hyperbolic viewer' info='\"Live scene; app.js sets positions through the scene access interface.\"'/>\n")
    s.append(" <NavigationInfo type='\"EXAMINE\" \"ANY\"' headlight='true' speed='1'/>\n")
    s.append(" <Viewpoint DEF='Home' description='Overview' position='0 0 3.15' centerOfRotation='0 0 0' fieldOfView='0.75'/>\n")
    sky = " ".join(f"{k}Url='\"sky/{k}.png\"'" for k in ("front", "back", "left", "right", "top", "bottom"))
    s.append(f" <Background skyColor='{rgb(BG)}' {sky}/>\n")
    s.append(f" <Fog color='{rgb(BG)}' fogType='LINEAR' visibilityRange='{FOG_RANGE}'/>\n")
    s.append(" <DirectionalLight direction='-0.45 -0.55 -0.7' intensity='0.6' color='1 0.95 0.88'/>\n")
    s.append(" <DirectionalLight direction='0.6 0.5 0.4' intensity='0.25' color='0.75 0.85 1'/>\n")
    s.append(" <ProximitySensor DEF='Eye' size='1000 1000 1000'/>\n")
    s.append(" <Transform DEF='Spin'>\n")
    s.append(boundary())
    s.append(f"  <Shape DEF='Atmosphere'><Appearance><UnlitMaterial transparency='0.5'/>{ADDITIVE}"
             f"{shader('AtmosphereShader', 'RimVS', 'RimFS', RIM_VS, RIM_FS, '0.53 0.71 0.94', 0.55, 3.2, True, True, fog=False)}"
             f"</Appearance>{uv_sphere(1.0, 96, 48)}</Shape>\n")
    s.append(" <Group DEF='Nodes'>\n")
    for g, item in enumerate(legend):
        s.append(instanced(f"G{g}", item["hex"], first=(g == 0)))
    for b, h in enumerate(HEAT):
        s.append(instanced(f"H{b}", h))
    s.append(" </Group>\n <Group DEF='Halos'>\n")
    first = True
    for name, hexcol in [(f"HG{g}", it["hex"]) for g, it in enumerate(legend)] + [(f"HH{b}", h) for b, h in enumerate(HEAT)]:
        s.append(f"  <InstancedShape DEF='{name}'><Appearance><UnlitMaterial transparency='0.5'/>{ADDITIVE}"
                 f"{shader(name + 'Shader', 'HaloVS', 'GlowFS', HALO_VS, GLOW_FS, rgb(hexcol), 0.42, 2.6, first, first)}"
                 f"</Appearance><Sphere USE='NodeBall'/></InstancedShape>\n")
        first = False
    s.append(" </Group>\n")
    s.append(f"  <Transform DEF='FocusMark' scale='0 0 0'><Shape><Appearance><UnlitMaterial transparency='0.5'/>{ADDITIVE}"
             f"{shader('FocusShader', 'RimVS', 'RimFS', '', '', '0.94 0.94 0.92', 0.9, 2.2, False, False, fog=False)}"
             f"</Appearance><Sphere DEF='MarkBall' radius='1.3'/></Shape></Transform>\n")
    s.append(f"  <Transform DEF='HoverMark' scale='0 0 0'><Shape><Appearance><UnlitMaterial transparency='0.5'/>{ADDITIVE}"
             f"{shader('HoverShader', 'RimVS', 'RimFS', '', '', '0.94 0.94 0.92', 0.55, 2.2, False, False, fog=False)}"
             f"</Appearance><Sphere USE='MarkBall'/></Shape></Transform>\n")
    s.append(lineset("Edges", 0.0))
    s.append(lineset("Alts", 0.0))
    s.append(lineset("Flows", 0.15))
    s.append(f"  <Shape DEF='FlowDotsShape'><Appearance><UnlitMaterial/><PointProperties pointSizeScaleFactor='3.2' pointSizeMinValue='1' pointSizeMaxValue='6'/>{ADDITIVE}</Appearance>"
             "<PointSet DEF='FlowDots'><Coordinate DEF='FlowDotsCoord'/><Color DEF='FlowDotsColor'/></PointSet></Shape>\n")
    s.append("  <Switch DEF='ShellSwitch' whichChoice='-1'>\n   <Group>\n")
    pts, idx, cidx = shell_mesh(d["shell"], inward=True)
    s.append("    <Shape DEF='ShellShape'><Appearance><UnlitMaterial transparency='0.55'/></Appearance>\n")
    s.append(f"     <IndexedFaceSet DEF='ShellFaces' solid='true' ccw='true' colorPerVertex='false' coordIndex='{' '.join(map(str, idx))}' colorIndex='{' '.join(map(str, cidx))}'>\n")
    s.append(f"      <Coordinate point='{', '.join(fmt(p) for p in pts)}'/>\n")
    s.append(f"      <Color DEF='ShellColor' color='{', '.join(rgb('#1a1a19') for _ in d['shell'])}'/>\n")
    s.append("     </IndexedFaceSet></Shape>\n")
    s.append(f"    <Shape DEF='Ribbons'><Appearance><UnlitMaterial transparency='0.3'/>{ADDITIVE}</Appearance>\n")
    s.append("     <IndexedFaceSet DEF='RibbonFaces' solid='false' colorPerVertex='true'><Coordinate DEF='RibbonCoord'/><Color DEF='RibbonColor'/></IndexedFaceSet></Shape>\n")
    s.append("   </Group>\n  </Switch>\n")
    s.append(" </Transform>\n")
    s.append(" </Scene>\n</X3D>\n")
    open(out, "w", encoding="utf-8").write("".join(s))


def instanced(name, hexcol, first=False):
    geo = "<Sphere DEF='NodeBall' radius='1'/>" if first else "<Sphere USE='NodeBall'/>"
    return (f"  <InstancedShape DEF='{name}'>\n   <Appearance><Material diffuseColor='{rgb(hexcol)}' emissiveColor='{rgb(hexcol, 0.3)}' "
            f"specularColor='0.55 0.55 0.55' shininess='0.6'/></Appearance>\n   {geo}\n  </InstancedShape>\n")


def lineset(name, transparency, width=None):
    lp = f"<LineProperties linewidthScaleFactor='{width}'/>" if width else ""
    return (f"  <Shape DEF='{name}Shape'><Appearance><UnlitMaterial transparency='{transparency}'/>{lp}</Appearance>"
            f"<IndexedLineSet DEF='{name}' colorPerVertex='true'><Coordinate DEF='{name}Coord'/><Color DEF='{name}Color'/></IndexedLineSet></Shape>\n")


def uv_sphere(r, nu, nv):
    """Finely tessellated sphere with normals (the primitive Sphere follows the browser's quality setting)."""
    pts, nrm, idx = [], [], []
    for v in range(nv + 1):
        th = math.pi * v / nv
        for u in range(nu + 1):
            ph = 2 * math.pi * u / nu
            n = (math.sin(th) * math.cos(ph), math.cos(th), math.sin(th) * math.sin(ph))
            nrm.append(n)
            pts.append(tuple(r * c for c in n))
    for v in range(nv):
        for u in range(nu):
            a = v * (nu + 1) + u
            idx += [a, a + nu + 1, a + 1, a + 1, a + nu + 1, a + nu + 2]
    return (f"<IndexedTriangleSet solid='false' index='{' '.join(map(str, idx))}'>"
            f"<Coordinate point='{', '.join(fmt(p) for p in pts)}'/><Normal vector='{', '.join(fmt(n) for n in nrm)}'/></IndexedTriangleSet>")


def boundary():
    pts, idx = [], []
    k = 120
    for plane in range(3):
        base = len(pts)
        for i in range(k):
            a = 2 * math.pi * i / k
            c, sn = math.cos(a), math.sin(a)
            pts.append([(c, sn, 0), (c, 0, sn), (0, c, sn)][plane])
        idx += list(range(base, base + k)) + [base, -1]
    return (" <Shape DEF='Boundary'><Appearance><UnlitMaterial emissiveColor='0.2 0.2 0.19' transparency='0.2'/></Appearance>"
            f"<IndexedLineSet coordIndex='{' '.join(map(str, idx))}'><Coordinate point='{', '.join(fmt(p) for p in pts)}'/></IndexedLineSet></Shape>\n")


def write_static(d, colors, out):
    n = d["nodes"]
    N = len(n["label"])
    spread = d["meta"]["spread_default"]
    F = frames_root(n, spread)
    X = [f[:, 3] for f in F]
    P = [ball(x) for x in X]
    bc = colors["branch_colors"]
    legend = colors["legend"]
    gi = {item["label"]: g for g, item in enumerate(legend)}
    tops = [i for i in range(N) if n["parent"][i] == 0]
    group_of_branch = {i: gi[n["label"][i]] for i in tops}
    nmax = max(n["n"])
    s = [head("WikiData.x3d: Wikipedia type tree in hyperbolic space (static)", [("EnvironmentalEffects", 3), ("Text", 1)])]
    m = d["meta"]
    info = f'"{m["snapshot"]}" "{m["articles_typed"]:,} typed articles in {N:,} visible classes"'
    s.append(f" <WorldInfo title='WikiData.x3d hyperbolic type tree' info={quoteattr(info)}/>\n")
    s.append(" <NavigationInfo type='\"EXAMINE\" \"ANY\"' headlight='true'/>\n")
    sky = " ".join(f"{k}Url='\"sky/{k}.png\"'" for k in ("front", "back", "left", "right", "top", "bottom"))
    s.append(f" <Background skyColor='{rgb(BG)}' {sky}/>\n")
    s.append(f" <Fog color='{rgb(BG)}' fogType='LINEAR' visibilityRange='{FOG_RANGE}'/>\n")
    s.append(" <DirectionalLight direction='-0.45 -0.55 -0.7' intensity='0.6' color='1 0.95 0.88'/>\n")
    s.append(" <Viewpoint DEF='Overview' description='Overview: the root (Thing) in focus' position='0 0 3.15' centerOfRotation='0 0 0' fieldOfView='0.75'/>\n")
    for i in tops:
        p = P[i]
        dirn = p / (np.linalg.norm(p) + 1e-12)
        pos = p + dirn * 0.9
        s.append(f" <Viewpoint description={quoteattr(n['label'][i])} position='{fmt(pos)}' orientation='{look_at(pos, p)}' centerOfRotation='{fmt(p)}' fieldOfView='0.75'/>\n")
    s.append(boundary())
    s.append(" <Group DEF='Classes'>\n")
    for g, item in enumerate(legend):
        s.append(f"  <Shape DEF='S{g}'><Appearance><Material diffuseColor='{rgb(item['hex'])}' emissiveColor='{rgb(item['hex'], 0.32)}' specularColor='0.25 0.25 0.25' shininess='0.35'/></Appearance><Sphere radius='1'/></Shape>\n")
    s.append("  <Switch whichChoice='-1'><Group/></Switch>\n")
    for i in range(N):
        g = group_of_branch.get(n["branch"][i]) if i else None
        p = P[i]
        r2 = float(p @ p)
        e = euclid_radius(spread * n["rho"][i], r2)
        if e < 2e-4:
            continue
        if i == 0:
            s.append(f"  <Transform translation='0 0 0' scale='{e:.5f} {e:.5f} {e:.5f}'><Shape><Appearance><Material diffuseColor='{rgb('#f0efec')}' emissiveColor='{rgb('#f0efec', 0.3)}'/></Appearance><Sphere radius='1'/></Shape></Transform>\n")
            continue
        s.append(f"  <Transform translation='{fmt(p)}' scale='{e:.5f} {e:.5f} {e:.5f}'><Shape USE='S{g}'/></Transform>\n")
    s.append(" </Group>\n")
    # edges as geodesic arcs, colored by branch
    pts, idx, cols = [], [], []
    for i in range(1, N):
        arc = geodesic(X[n["parent"][i]], X[i], 4)
        base = len(pts)
        pts += arc
        idx += list(range(base, base + len(arc))) + [-1]
        col = rgb(bc[str(n["branch"][i])], 0.55)
        cols += [col] * len(arc)
    s.append(" <Shape DEF='Edges'><Appearance><UnlitMaterial/></Appearance>\n")
    s.append(f"  <IndexedLineSet colorPerVertex='true' coordIndex='{' '.join(map(str, idx))}'>\n")
    s.append(f"   <Coordinate point='{', '.join(fmt(p) for p in pts)}'/>\n   <Color color='{', '.join(cols)}'/>\n  </IndexedLineSet></Shape>\n")
    # labels: root, top-level and second-level classes
    s.append(" <Group DEF='Labels'>\n")
    font_defined = False
    for i in range(N):
        if n["depth"][i] > 2 and i != 0:
            continue
        if n["parent"][i] > 0 and n["n"][i] < 20000:
            continue
        p = P[i]
        e = euclid_radius(spread * n["rho"][i], float(p @ p))
        size = max(0.018, min(0.06, e * 0.9))
        font = "<FontStyle USE='LabelFont'/>" if font_defined else "<FontStyle DEF='LabelFont' family='\"SANS\"' justify='\"BEGIN\" \"MIDDLE\"' size='1'/>"
        font_defined = True
        off = p + np.array([e * 1.15, 0, 0])
        s.append(f"  <Transform translation='{fmt(off)}' scale='{size:.4f} {size:.4f} {size:.4f}'><Billboard axisOfRotation='0 0 0'><Shape>"
                 f"<Appearance><UnlitMaterial emissiveColor='{rgb('#f0efec')}'/></Appearance><Text string={quoteattr(json.dumps(n['label'][i]))}>{font}</Text></Shape></Billboard></Transform>\n")
    s.append(" </Group>\n")
    # topic shell, visible from both sides, colored by topic in alternating neutral steps
    pts, idx, cidx = shell_mesh(d["shell"], inward=False)
    shades = ["#2c2c2a", "#383835", "#444441", "#52514e"]
    cl = [rgb(shades[(p["topic"] + k) % 4]) for k, p in enumerate(d["shell"])]
    s.append(" <Shape DEF='TopicShell'><Appearance><UnlitMaterial transparency='0.55'/></Appearance>\n")
    s.append(f"  <IndexedFaceSet solid='false' colorPerVertex='false' coordIndex='{' '.join(map(str, idx))}' colorIndex='{' '.join(map(str, cidx))}'>\n")
    s.append(f"   <Coordinate point='{', '.join(fmt(p) for p in pts)}'/>\n   <Color color='{', '.join(cl)}'/>\n  </IndexedFaceSet></Shape>\n")
    s.append(" <Group DEF='TopicLabels'>\n")
    topics = d["meta"]["topics"]
    for ti, tp in enumerate(topics):
        ps = [p for p in d["shell"] if p["topic"] == ti]
        az = (ps[0]["az0"] + ps[0]["az1"]) / 2
        la = max(p["lat1"] for p in ps) + 0.08
        r = SHELL_R * 1.01
        pos = (r * math.cos(la) * math.cos(az), r * math.sin(la), r * math.cos(la) * math.sin(az))
        s.append(f"  <Transform translation='{fmt(pos)}' scale='0.055 0.055 0.055'><Billboard axisOfRotation='0 0 0'><Shape><Appearance><UnlitMaterial emissiveColor='{rgb('#c3c2b7')}'/></Appearance>"
                 f"<Text string={quoteattr(json.dumps(tp))}><FontStyle family='\"SANS\"' justify='\"MIDDLE\" \"MIDDLE\"' size='1'/></Text></Shape></Billboard></Transform>\n")
    s.append(" </Group>\n </Scene>\n</X3D>\n")
    open(out, "w", encoding="utf-8").write("".join(s))


def main(data_path, colors_path, out_dir):
    d = json.load(open(data_path, encoding="utf-8"))
    colors = json.load(open(colors_path))
    colors["legend"] = [{"label": "Thing", "hex": "#f0efec", "group": "root"}] + colors["legend"]
    os.makedirs(out_dir, exist_ok=True)
    live, static = os.path.join(out_dir, "scene.x3d"), os.path.join(out_dir, "hyperball_static.x3d")
    json.dump(colors["legend"], open(os.path.join(out_dir, "legend.json"), "w"), indent=1)
    write_live(d, colors, live)
    write_static(d, colors, static)
    log("build", "wrote scene.x3d (live, X_ITE InstancedShape) and hyperball_static.x3d (X3D 4.0)", mark="C",
        bytes={"scene.x3d": os.path.getsize(live), "hyperball_static.x3d": os.path.getsize(static)})
    print(os.path.getsize(live), os.path.getsize(static))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
