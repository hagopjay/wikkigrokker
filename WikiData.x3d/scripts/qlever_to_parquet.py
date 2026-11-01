#!/usr/bin/env python3
"""Convert the QLever TSV exports to Parquet with bare Q-ids.

usage: python3 -I qlever_to_parquet.py QLEVER_DIR OUT_DIR

Reads  item_types.tsv  (?item ?type)        -> item_types.parquet   (item, type)
       class_edges.tsv (?c ?p)              -> class_edges.parquet  (c, p)      c subclass-of p
       class_labels.tsv (?c ?label)         -> class_labels.parquet (c, label)
       type_counts.tsv (?t ?n)              -> type_counts.parquet  (t, n)
Rows whose ids are not plain Q-numbers (blank nodes, 'unknown value') are dropped and counted.
"""
import sys
import time
from pathlib import Path

import duckdb


def main(qdir, out):
    qdir, out = Path(qdir), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("pragma threads=2")

    def tsv(name, cols):
        # quote='' keeps the literal quotes; all_varchar avoids type guessing on '"Q1"' style values
        return f"read_csv('{qdir}/{name}.tsv', delim='\\t', header=true, quote='', all_varchar=true, columns={cols})"

    jobs = {
        "item_types": (
            "select regexp_extract(a, 'Q[0-9]+', 0) as item, regexp_extract(b, 'Q[0-9]+', 0) as type",
            tsv("item_types", "{'a':'VARCHAR','b':'VARCHAR'}"), "item <> '' and type <> ''"),
        "class_edges": (
            "select regexp_extract(a, 'Q[0-9]+', 0) as c, regexp_extract(b, 'Q[0-9]+', 0) as p",
            tsv("class_edges", "{'a':'VARCHAR','b':'VARCHAR'}"), "c <> '' and p <> '' and c <> p"),
        "class_labels": (
            "select regexp_extract(a, 'Q[0-9]+', 0) as c, regexp_extract(b, '^\"(.*)\"@en$', 1) as label",
            tsv("class_labels", "{'a':'VARCHAR','b':'VARCHAR'}"), "c <> '' and label <> ''"),
        "type_counts": (
            "select regexp_extract(a, 'Q[0-9]+', 0) as t, cast(b as bigint) as n",
            tsv("type_counts", "{'a':'VARCHAR','b':'VARCHAR'}"), "t <> ''"),
    }
    for name, (sel, src, cond) in jobs.items():
        t0 = time.time()
        # the TSV has a header line: skip it by reading with header=true and our own column names
        raw = con.execute(f"select count(*) from {src}").fetchone()[0]
        con.execute(f"""copy (select * from ({sel} from {src}) where {cond})
                        to '{out}/{name}.parquet' (format parquet, compression zstd)""")
        kept = con.execute(f"select count(*) from read_parquet('{out}/{name}.parquet')").fetchone()[0]
        print(f"{name}: {raw} rows read, {kept} kept, {raw - kept} dropped, {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
