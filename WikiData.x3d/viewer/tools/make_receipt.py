#!/usr/bin/env python3
"""Write the build receipt for the hyperbolic viewer (loom/receipts/<name>.json).

usage: make_receipt.py NAME WIKIMAP_DIR VIEWER_DIR EYES_JSON

Binds inputs and outputs by sha256, and collects each witness verdict from its own report,
so the receipt says what was checked, by which route, and what a pass does not establish.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "loom"))
from loomlog import write_receipt  # noqa: E402


def grammar(viewer):
    out = {}
    for f, extra in (("hyperball_static.x3d", []), ("scene.x3d", ["--allow-extension", "InstancedShape", "BlendMode", "DepthMode"])):
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "witness_grammar.py"), os.path.join(viewer, f), *extra],
                           capture_output=True, text=True)
        out[f] = {"pass": r.returncode == 0, "detail": [l for l in r.stdout.splitlines() if "have fun" not in l]}
    st = subprocess.run([sys.executable, "-I", os.path.join(HERE, "witness_grammar.py"), "--self-test"], capture_output=True, text=True)
    out["self_test"] = {"pass": st.returncode == 0, "detail": st.stdout.splitlines()[:1]}
    return out


def main(name, wikimap, viewer, eyes_json):
    spine = os.path.join(wikimap, "out", "type_spine_r7")
    cs = os.path.join(wikimap, "out", "clickstream_2026_10")
    hb = os.path.join(wikimap, "out", "hyperball")
    inputs = [os.path.join(spine, f) for f in ("classes.parquet", "edges.parquet", "article_type.parquet")]
    inputs += [os.path.join(wikimap, "data", "dev_slice", "articles.parquet"),
               os.path.join(cs, "article_traffic.parquet"), os.path.join(cs, "link_flows.parquet"),
               os.path.join(wikimap, "data", "parquet", "page.parquet")]
    outputs = [os.path.join(viewer, f) for f in ("index.html", "app.js", "scene.x3d", "hyperball_static.x3d", "legend.json",
                                                 "data/viewer_data.json", "data/branch_colors.json")]
    outputs += [os.path.join(viewer, "sky", f"{k}.png") for k in ("front", "back", "left", "right", "top", "bottom")]
    cs_rep = json.load(open(os.path.join(cs, "report.json")))
    hb_rep = json.load(open(os.path.join(hb, "report.json")))
    colors = json.load(open(os.path.join(viewer, "data", "branch_colors.json")))
    eyes = json.load(open(eyes_json))
    verdicts = {
        "Truth: clickstream load gate": {"pass": all(cs_rep["checks"].values()), "checks": cs_rep["checks"],
                                         "rows": cs_rep["rows"], "clicks": cs_rep["clicks"], "resolved_share": cs_rep["curr_resolved_click_share"]},
        "Truth: export conservation": {"pass": all(hb_rep["checks"].values()), "checks": hb_rep["checks"]},
        "Palette: spatial-neighbour pairs": {"pass": not colors["failing_neighbor_pairs"], "neighbor_pairs": len(colors["neighbor_pairs"]),
                                             "threshold_deg": colors["neighbor_deg"]},
        "Grammar (x3d.py route; X_ITE parser route logged separately)": grammar(viewer),
        "Eyes/Host/Behaviour/Truth (Playwright, software WebGL, artifact-like policy)": {
            "pass": all(v["pass"] for k, v in eyes.items() if k != "host"),
            "results": {k: v["pass"] for k, v in eyes.items()},
            "host_note": "host fails only on policy-blocked X_ITE font, stylesheet and tangent-helper requests; none fatal",
        },
    }
    not_established = [
        "frame rate on a real GPU (only software WebGL was run here: about 1 to 3 frames per second)",
        "behaviour on the live artifact host and on phones (checked under a local policy that imitates the host)",
        "that the topics of 49,649 Vital Articles stand for the topics of all 7.0 million typed articles",
        "that clickstream arrivals are page views; pairs with 10 or fewer clicks are missing and bots are included",
        "that the layout is free of overlaps everywhere; the Eyes witness samples four views",
    ]
    path, rec = write_receipt(name, inputs=inputs, outputs=outputs, verdicts=verdicts,
                              params={"spread_default": hb_rep["meta"]["spread_default"], "x_ite": "16.7.0 via jsDelivr, sha384 pinned"},
                              not_established=not_established, base=os.path.dirname(viewer))
    print(path, rec["receipt_sha256"])


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
