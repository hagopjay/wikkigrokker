#!/usr/bin/env python3
"""Pull specific pages out of the multistream dump with HTTP range requests.

usage: extract_dump_pages.py RANGES.txt TITLES.txt DUMP_URL OUT.jsonl

RANGES.txt   lines "start next_start" (byte offsets of consecutive multistream blocks)
TITLES.txt   one wanted page title per line (spaces or underscores)
DUMP_URL     URL of the pages-articles-multistream .xml.bz2 file (must support Range)
OUT.jsonl    one JSON object per found page: title, ns, id, redirect, text
"""
import bz2
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ranges_path, titles_path, url, out_path = sys.argv[1:5]
UA = "wiki-map-iteration0/0.1 (https://claude.ai; research planning)"

with open(titles_path, encoding="utf-8") as f:
    wanted = {line.rstrip("\n").replace("_", " ") for line in f if line.strip()}
with open(ranges_path, encoding="utf-8") as f:
    ranges = [tuple(map(int, line.split())) for line in f if line.strip()]

found = 0
with open(out_path, "w", encoding="utf-8") as out:
    for i, (start, nxt) in enumerate(ranges, 1):
        req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{nxt - 1}", "User-Agent": UA})
        blob = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    blob = resp.read()
                break
            except Exception as exc:  # network hiccup: back off and retry
                print(f"range {start}: {exc!r}; retry {attempt + 1}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        if blob is None:
            sys.exit(f"giving up on range {start}")
        xml = bz2.decompress(blob).decode("utf-8")
        root = ET.fromstring("<mediawiki>" + xml + "</mediawiki>")
        for page in root.iter("page"):
            title = page.findtext("title")
            if title in wanted:
                rev = page.find("revision")
                text = rev.findtext("text") if rev is not None else ""
                out.write(json.dumps({
                    "title": title, "ns": page.findtext("ns"), "id": page.findtext("id"),
                    "redirect": page.find("redirect") is not None, "text": text or "",
                }, ensure_ascii=False) + "\n")
                found += 1
        if i % 20 == 0:
            print(f"{i}/{len(ranges)} blocks, {found} pages", file=sys.stderr)
print(f"{found} pages written to {out_path}")
