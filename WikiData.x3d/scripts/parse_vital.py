#!/usr/bin/env python3
"""Parse the Wikipedia Vital Articles pages into Parquet (gold topic placement).

usage: python3 -I parse_vital.py PAGES.jsonl OUTDIR

PAGES.jsonl  one JSON object per line {title, ns, id, redirect, text}, written by extract_dump_pages.py
OUTDIR       new directory for the outputs:
  vital_json.parquet      one row per article in Wikipedia:Vital articles/data/{A..Z,others}.json
                          (title, level, topic, section, sublist) - the project's own machine-readable placement
  vital_tree.parquet      one row per list item in the Level 1-5 list pages: page, heading path,
                          list nesting (parent item), annotated level, class icons
  vital_headings.parquet  one row per heading in those pages (with the article count the page states)
  vital_summary.json      counts and cross-checks between the two sources

All input is treated as untrusted data: it is only parsed as text/JSON, never executed.
"""
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

PREFIX = "Wikipedia:Vital articles/"
HEAD = re.compile(r"^(={1,6})\s*(.*?)\s*=+\s*$")
ITEM = re.compile(r"^(#+|\*+)(?![#*:;])\s*(.*)$")
ICON = re.compile(r"\{\{\s*Icon\s*\|\s*([^}|]+?)\s*(?:\|[^}]*)?\}\}", re.I)
LVLNOTE = re.compile(r"\[\[\s*Wikipedia:Vital[ _]articles/Level[ _](\d)\s*(?:\|[^\]]*)?\]\]", re.I)
LINK = re.compile(r"\[\[\s*:?([^\[\]|#]+?)\s*(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")
COUNT = re.compile(r"\{\{\s*Vital article count\s*\|\s*(\d[\d,]*)", re.I)
NS_SKIP = re.compile(
    r"^(wikipedia|wp|category|file|image|template|help|portal|draft|user|talk|special|"
    r"wikt|commons|meta|wikidata)\s*:",
    re.I,
)
SKIP_PAGE = ("Article alerts", "/Nav bar", "/Removed", "/Statistics", "/Popular pages")


def norm_title(t):
    t = html.unescape(t).replace("\xa0", " ").replace("_", " ").strip()
    t = re.sub(r"\s+", " ", t)
    return t[:1].upper() + t[1:] if t else t


def clean_heading(s):
    s = re.sub(r"<!--.*?-->", "", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"'{2,}", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    n = None
    m = re.search(r"\s*\(\s*(\d[\d,]*)\s+articles?\s*\)\s*$", s)
    if m:
        n = int(m.group(1).replace(",", ""))
        s = s[: m.start()]
    return re.sub(r"\s+", " ", s).strip(), n


def page_level_and_topic(title):
    """'Wikipedia:Vital articles/Level 5/People/Sports figures' -> (5, 'People/Sports figures')."""
    rest = title[len(PREFIX):]
    m = re.match(r"Level (\d)(?:/(.*))?$", rest)
    if not m:
        return None, None
    return int(m.group(1)), (m.group(2) or "")


def parse_item(body):
    icons = ICON.findall(body)
    m = LVLNOTE.search(body)
    note = int(m.group(1)) if m else None
    stripped = ICON.sub("", body)
    stripped = LVLNOTE.sub("", stripped)
    title = None
    for lm in LINK.finditer(stripped):
        if NS_SKIP.match(lm.group(1)):
            continue
        title = norm_title(lm.group(1))
        break
    bold = "'''" in body
    return title, note, bold, icons


def main(pages_path, outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=False)
    pages = {}
    with open(pages_path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not r["redirect"]:
                pages[r["title"]] = r["text"]

    # ---- 1. machine-readable JSON pages
    jrows, bad = [], []
    for t, text in pages.items():
        if not (t.startswith(PREFIX + "data/") and t.endswith(".json")):
            continue
        if "Topic hierarchy" in t:
            continue
        try:
            d = json.loads(text)
        except ValueError as e:
            bad.append((t, str(e)[:80]))
            continue
        for k, v in d.items():
            jrows.append({
                "title": norm_title(k), "raw_key": k, "level": int(v["level"]),
                "topic": v.get("topic"), "section": v.get("section"),
                "sublist": v.get("sublist"), "src": t.rsplit("/", 1)[1],
            })
    pq.write_table(pa.Table.from_pylist(jrows), out / "vital_json.parquet", compression="zstd")

    # ---- 2. list pages -> headings + items
    items, heads = [], []
    stats = Counter()
    for t in sorted(pages):
        if not t.startswith(PREFIX + "Level") or any(s in t for s in SKIP_PAGE):
            continue
        plevel, ptopic = page_level_and_topic(t)
        if plevel is None or t.endswith("Level/header"):
            continue
        hstack = []          # [(level, text)]
        lstack = []          # list ancestors, index = depth-1
        last_head = None
        seq = 0
        for lineno, line in enumerate(pages[t].splitlines(), 1):
            hm = HEAD.match(line)
            if hm:
                text, n = clean_heading(hm.group(2))
                lvl = len(hm.group(1))
                while hstack and hstack[-1][0] >= lvl:
                    hstack.pop()
                hstack.append((lvl, text))
                lstack = []
                heads.append({"page": t, "page_level": plevel, "seq": len(heads), "line": lineno,
                              "level": lvl, "heading": text,
                              "path": " > ".join(h[1] for h in hstack),
                              "n_in_title": n, "n_stated": None})
                last_head = len(heads) - 1
                continue
            cm = COUNT.search(line)
            if cm and last_head is not None and heads[last_head]["n_stated"] is None:
                heads[last_head]["n_stated"] = int(cm.group(1).replace(",", ""))
                continue
            im = ITEM.match(line)
            if not im:
                continue
            depth = len(im.group(1))
            title, note, bold, icons = parse_item(im.group(2))
            if title is None:
                stats["items_without_title"] += 1
                continue
            lstack = lstack[: depth - 1]
            while len(lstack) < depth - 1:
                lstack.append(None)
            parent = lstack[depth - 2] if depth >= 2 else None
            lstack.append(title)
            seq += 1
            items.append({
                "page": t, "page_level": plevel, "page_topic": ptopic, "seq": seq, "line": lineno,
                "heading_path": " > ".join(h[1] for h in hstack),
                "heading_depth": len(hstack), "list_depth": depth, "parent": parent,
                "title": title, "level_note": note, "bold": bold, "classes": ",".join(icons),
            })
    pq.write_table(pa.Table.from_pylist(items), out / "vital_tree.parquet", compression="zstd")
    pq.write_table(pa.Table.from_pylist(heads), out / "vital_headings.parquet", compression="zstd")

    # ---- 3. cross-checks
    jt = {r["title"]: r for r in jrows}
    tt = Counter(i["title"] for i in items)
    in_both = sum(1 for k in jt if k in tt)
    only_json = [k for k in jt if k not in tt]
    only_tree = [k for k in tt if k not in jt]
    lvl_json = Counter(r["level"] for r in jrows)
    summary = {
        "json_pages_failed": bad,
        "json_articles": len(jrows), "json_unique_titles": len(jt),
        "json_by_level": dict(sorted(lvl_json.items())),
        "json_with_sublist": sum(1 for r in jrows if r["sublist"]),
        "tree_pages": len({i["page"] for i in items}), "tree_items": len(items),
        "tree_unique_titles": len(tt), "tree_headings": len(heads),
        "tree_items_without_title": stats["items_without_title"],
        "tree_max_list_depth": max((i["list_depth"] for i in items), default=0),
        "tree_items_with_parent": sum(1 for i in items if i["parent"]),
        "titles_in_both": in_both,
        "titles_only_in_json": len(only_json), "examples_only_in_json": only_json[:25],
        "titles_only_in_tree": len(only_tree), "examples_only_in_tree": only_tree[:25],
        "items_per_page": dict(Counter(i["page"].replace(PREFIX, "") for i in items).most_common()),
    }
    (out / "vital_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "items_per_page"}, indent=1, ensure_ascii=False))
    print("items per page:")
    for k, v in summary["items_per_page"].items():
        print(f"  {v:6d}  {k}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
