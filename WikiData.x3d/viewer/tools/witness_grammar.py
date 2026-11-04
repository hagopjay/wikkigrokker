#!/usr/bin/env python3
"""Grammar witness: do the X3D scenes use real X3D 4.0 nodes and fields with legal values?

usage: witness_grammar.py FILE.x3d [FILE.x3d ...] [--allow-extension NODE ...]

Route 1 (this script): every element must be an X3D 4.0 node known to x3d.py (the Web3D
object model on PyPI); every attribute must be a field of that node; every value is
parsed by field type and assigned to an x3d.py object, which checks type and range
(colors in 0..1, transparency in 0..1, radius > 0, ...). DEF names must be unique and
every USE must follow its DEF.
Route 2 is X_ITE's own parser (x3d-tidy), run by the caller.

Two-sided: --self-test feeds a broken scene and fails unless every planted fault is caught.
What a pass does not establish: that the scene draws anything (Eyes), that X_ITE accepts
extension nodes (Host), or that containment is legal (route 2 covers parsing).
"""
import re
import sys
import xml.etree.ElementTree as ET

import x3d

STRUCTURAL = {"X3D", "head", "component", "meta", "unit", "Scene", "field"}
SKIP_ATTRS = {"DEF", "USE", "containerField", "class", "id", "style"}
VEC = {"SFVec2f": 2, "SFVec2d": 2, "SFVec3f": 3, "SFVec3d": 3, "SFVec4f": 4, "SFVec4d": 4,
       "SFColor": 3, "SFColorRGBA": 4, "SFRotation": 4}
MVEC = {"MFVec2f": 2, "MFVec2d": 2, "MFVec3f": 3, "MFVec3d": 3, "MFColor": 3, "MFColorRGBA": 4,
        "MFRotation": 4, "MFVec4f": 4}


def nums(s):
    return [float(x) for x in re.split(r"[\s,]+", s.strip()) if x]


def parse(ftype, s):
    if ftype == "SFBool":
        if s not in ("true", "false"):
            raise ValueError(f"SFBool must be true/false, got {s!r}")
        return s == "true"
    if ftype == "SFInt32":
        return int(s)
    if ftype in ("SFFloat", "SFDouble", "SFTime"):
        return float(s)
    if ftype == "SFString":
        return s
    if ftype == "MFString":
        vals = re.findall(r'"((?:[^"\\]|\\.)*)"', s)
        if s.strip() and not vals:
            raise ValueError(f"MFString values must be quoted: {s[:40]!r}")
        return vals
    if ftype in VEC:
        v = nums(s)
        if len(v) != VEC[ftype]:
            raise ValueError(f"{ftype} needs {VEC[ftype]} numbers, got {len(v)}")
        return tuple(v)
    if ftype in MVEC:
        v, k = nums(s), MVEC[ftype]
        if len(v) % k:
            raise ValueError(f"{ftype} length {len(v)} is not a multiple of {k}")
        return [tuple(v[i:i + k]) for i in range(0, len(v), k)]
    if ftype == "MFInt32":
        return [int(x) for x in re.split(r"[\s,]+", s.strip()) if x]
    if ftype in ("MFFloat", "MFDouble", "MFTime"):
        return nums(s)
    raise ValueError(f"unhandled field type {ftype}")


def check(path, allow=()):
    errors, extensions, n_el, n_attr = [], set(), 0, 0
    defs = set()
    root = ET.parse(path).getroot()
    if root.tag != "X3D" or root.get("version") != "4.0":
        errors.append("root must be <X3D version='4.0'>")
    for el in root.iter():
        tag = el.tag
        if tag in STRUCTURAL:
            continue
        n_el += 1
        if tag in allow:
            extensions.add(tag)
            continue
        cls = getattr(x3d, tag, None)
        if cls is None or not hasattr(cls, "FIELD_DECLARATIONS"):
            errors.append(f"unknown node <{tag}>")
            continue
        if el.get("DEF"):
            if el.get("DEF") in defs:
                errors.append(f"duplicate DEF {el.get('DEF')}")
            defs.add(el.get("DEF"))
        if el.get("USE"):
            if el.get("USE") not in defs:
                errors.append(f"USE {el.get('USE')} before its DEF")
            continue
        decl = {f[0]: f[2].__name__ for f in cls.FIELD_DECLARATIONS()}
        obj = cls()
        for k, v in el.attrib.items():
            if k in SKIP_ATTRS or k.startswith("{"):
                continue
            n_attr += 1
            if k not in decl:
                errors.append(f"<{tag}> has no field {k!r}")
                continue
            try:
                setattr(obj, k, parse(decl[k], v))
            except Exception as exc:  # x3d.py raises its own type and range errors
                errors.append(f"<{tag} {k}>: {str(exc)[:160]}")
    return {"file": path, "elements": n_el, "attributes": n_attr, "extensions": sorted(extensions), "errors": errors}


BROKEN = """<?xml version="1.0"?><X3D profile='Interactive' version='4.0'><Scene>
<Shape><Appearance><Material diffuseColor='1.5 0 0' diffuseColour='1 0 0' transparency='2'/></Appearance><Spheer radius='1'/></Shape>
<Shape USE='Nope'/><Transform translation='0 0'/><NavigationInfo headlight='yes'/></Scene></X3D>"""
PLANTED = 6  # SFColor range, unknown field, transparency range, unknown node, dangling USE, short vector (+ SFBool)


def self_test(tmpdir="/tmp"):
    p = f"{tmpdir}/witness_grammar_broken.x3d"
    open(p, "w").write(BROKEN)
    r = check(p)
    return len(r["errors"]) >= PLANTED, r


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--self-test"]:
        ok, r = self_test()
        print(("SELF-TEST PASS" if ok else "SELF-TEST FAIL") + f": caught {len(r['errors'])} of {PLANTED}+ planted faults")
        for e in r["errors"]:
            print("  ", e)
        sys.exit(0 if ok else 1)
    allow = []
    if "--allow-extension" in args:
        i = args.index("--allow-extension")
        allow = args[i + 1:]
        args = args[:i]
    bad = 0
    for f in args:
        r = check(f, allow)
        bad += len(r["errors"])
        print(f"{f}: {r['elements']} elements, {r['attributes']} attributes checked, "
              f"{len(r['errors'])} errors, extensions allowed: {r['extensions'] or 'none'}")
        for e in r["errors"][:30]:
            print("  ", e)
    sys.exit(1 if bad else 0)
