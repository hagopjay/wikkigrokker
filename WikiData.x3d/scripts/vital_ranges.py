#!/usr/bin/env python3
"""Find the multistream-dump blocks that hold the pages whose title starts with a prefix.

usage: python3 -I vital_ranges.py INDEX.txt.bz2 PREFIX OUT_DIR

INDEX.txt.bz2  enwiki-DATE-pages-articles-multistream-index.txt.bz2; lines are "offset:page_id:title"
               in dump order, one offset per block of about 100 pages
PREFIX         title prefix to select, e.g. "Wikipedia:Vital articles"
OUT_DIR        new directory that receives
                 titles.txt   every matching title, one per line (input of extract_dump_pages.py)
                 ranges.txt   "start next_start" for each block that holds a match (byte range to fetch)

A block's byte range ends where the next block starts, so the next distinct offset is tracked while
streaming.  A match in the very last block of the file would have no end offset; that case stops the run.
"""
import bz2
import sys
from pathlib import Path


def main(index, prefix, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    titles = []
    match_offsets = set()
    next_of = {}
    last = None
    n = 0
    with bz2.open(index, "rt", encoding="utf-8") as f:
        for line in f:
            n += 1
            off_s, _, rest = line.partition(":")
            off = int(off_s)
            if off != last:
                if last is not None:
                    next_of[last] = off
                last = off
            title = rest.partition(":")[2].rstrip("\n")
            if title.startswith(prefix):
                titles.append(title)
                match_offsets.add(off)
    missing = [o for o in match_offsets if o not in next_of]
    if missing:
        sys.exit(f"match in the last block ({missing}); its end offset is the dump's file size")
    ranges = sorted((o, next_of[o]) for o in match_offsets)
    (out / "titles.txt").write_text("\n".join(titles) + "\n", encoding="utf-8")
    (out / "ranges.txt").write_text("".join(f"{a} {b}\n" for a, b in ranges), encoding="utf-8")
    print(f"{n} index lines, {len(titles)} titles, {len(ranges)} blocks")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
