#!/usr/bin/env python3
"""Hash-chained event log and build receipts for WikiData.x3d (Loom style).

Every line of loom/events.jsonl is one event. Each event carries the hash of the
line before it, so editing or deleting any past line breaks `verify`.

Marks follow the Loom convention:
  C = ran or recomputed here,  L = stated in a document or note,  S = judgment.

Library use:
    from loomlog import log, write_receipt, file_record
    log("check", "clickstream gzip -t passed", mark="C", bytes=467888365)

CLI:
    loomlog.py log KIND "summary" [--mark C|L|S] [--json '{"k": "v"}']
    loomlog.py verify
    loomlog.py hash FILE [FILE ...]
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("WIKIGROKKER_ROOT", os.path.dirname(HERE))
LOG = os.path.join(ROOT, "loom", "events.jsonl")
RECEIPTS = os.path.join(ROOT, "loom", "receipts")
ZERO = "0" * 64
MARKS = {"C", "L", "S"}


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def file_record(path, base=None):
    st = os.stat(path)
    rel = os.path.relpath(path, base) if base else path
    return {
        "path": rel,
        "bytes": st.st_size,
        "sha256": sha256_file(path),
        "mtime_utc": datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc).isoformat(timespec="seconds"),
    }


def _read_events():
    if not os.path.exists(LOG):
        return []
    with open(LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def log(kind, summary, mark="C", **fields):
    if mark not in MARKS:
        raise ValueError(f"mark must be one of {sorted(MARKS)}, got {mark!r}")
    events = _read_events()
    prev = events[-1]["hash"] if events else ZERO
    entry = {
        "seq": len(events) + 1,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "summary": summary,
        "mark": mark,
        **fields,
        "prev": prev,
    }
    entry["hash"] = hashlib.sha256(prev.encode() + _canon(entry)).hexdigest()
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def verify():
    prev = ZERO
    events = _read_events()
    for i, e in enumerate(events, 1):
        body = {k: v for k, v in e.items() if k != "hash"}
        if e.get("prev") != prev or e.get("seq") != i:
            return False, len(events), i
        if hashlib.sha256(prev.encode() + _canon(body)).hexdigest() != e.get("hash"):
            return False, len(events), i
        prev = e["hash"]
    return True, len(events), None


def git_head(repo=None):
    repo = repo or os.path.dirname(ROOT)
    try:
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
        return {"commit": head, "dirty": dirty}
    except Exception as exc:  # no git: say so rather than guess
        return {"commit": None, "error": str(exc)}


def write_receipt(name, inputs=(), outputs=(), verdicts=None, params=None, not_established=(), notes=None, base=None):
    """Write loom/receipts/<name>.json and log it. Verdicts map witness -> {"pass": bool, ...}."""
    rec = {
        "receipt": "wikidata.x3d/1",
        "name": name,
        "built_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "code": git_head(),
        "params": params or {},
        "inputs": [file_record(p, base) for p in inputs],
        "outputs": [file_record(p, base) for p in outputs],
        "verdicts": verdicts or {},
        "what_a_pass_does_not_establish": list(not_established),
        "notes": notes,
    }
    rec["receipt_sha256"] = hashlib.sha256(_canon(rec)).hexdigest()
    os.makedirs(RECEIPTS, exist_ok=True)
    path = os.path.join(RECEIPTS, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
        f.write("\n")
    failed = [k for k, v in rec["verdicts"].items() if isinstance(v, dict) and v.get("pass") is False]
    log("receipt", f"receipt {name}: {len(rec['outputs'])} outputs, {len(failed)} failed verdicts",
        mark="C", receipt=os.path.relpath(path, ROOT), receipt_sha256=rec["receipt_sha256"], failed=failed)
    return path, rec


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("log")
    a.add_argument("kind")
    a.add_argument("summary")
    a.add_argument("--mark", default="C")
    a.add_argument("--json", default="{}")
    sub.add_parser("verify")
    h = sub.add_parser("hash")
    h.add_argument("files", nargs="+")
    args = ap.parse_args()
    if args.cmd == "log":
        e = log(args.kind, args.summary, mark=args.mark, **json.loads(args.json))
        print(e["seq"], e["hash"][:12])
    elif args.cmd == "verify":
        ok, n, bad = verify()
        print(f"chain {'intact' if ok else 'BROKEN at seq ' + str(bad)}: {n} events")
        sys.exit(0 if ok else 1)
    elif args.cmd == "hash":
        for p in args.files:
            print(json.dumps(file_record(p)))


if __name__ == "__main__":
    main()
