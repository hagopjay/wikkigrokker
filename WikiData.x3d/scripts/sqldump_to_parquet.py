#!/usr/bin/env python3
"""Stream a MediaWiki SQL dump into Parquet.

The dump lists one row per line, as "(1,'Title',0),". Pre-strip it in the shell so
this script receives bare CSV rows:

    LC_ALL=C gzip -dc enwiki-latest-TABLE.sql.gz | LC_ALL=C grep -a '^(' \
        | LC_ALL=C sed -E 's/^\\(//; s/\\)[,;]$//' \
        | python3 -I sqldump_to_parquet.py TABLE OUT.parquet

(or use run_table.sh, which does exactly that.)

Only the columns listed in SPEC are kept. Rows that do not parse are skipped and
counted, never repaired. The file is written as OUT.parquet.partial and renamed
when the stream ends cleanly, so a killed run never leaves a file that looks complete.
"""
import json
import os
import sys
import time

import pyarrow as pa
import pyarrow.compute as pcomp
import pyarrow.csv as pc
import pyarrow.parquet as pq

I32, I64, STR, BIN = pa.int32(), pa.int64(), pa.string(), pa.binary()

# table -> (all columns in dump order, {kept column: arrow type})
SPEC = {
    "category": (
        ["cat_id", "cat_title", "cat_pages", "cat_subcats", "cat_files"],
        {"cat_id": I64, "cat_title": BIN, "cat_pages": I32, "cat_subcats": I32, "cat_files": I32},
    ),
    "categorylinks": (
        ["cl_from", "cl_sortkey", "cl_timestamp", "cl_sortkey_prefix", "cl_type", "cl_collation_id", "cl_target_id"],
        {"cl_from": I64, "cl_type": STR, "cl_target_id": I64},
    ),
    "linktarget": (
        ["lt_id", "lt_namespace", "lt_title"],
        {"lt_id": I64, "lt_namespace": I32, "lt_title": BIN},
    ),
    "page": (
        ["page_id", "page_namespace", "page_title", "page_is_redirect", "page_is_new", "page_random",
         "page_touched", "page_links_updated", "page_latest", "page_len", "page_content_model", "page_lang"],
        {"page_id": I64, "page_namespace": I32, "page_title": BIN, "page_is_redirect": I32, "page_len": I64},
    ),
    "page_props": (
        ["pp_page", "pp_propname", "pp_value", "pp_sortkey"],
        {"pp_page": I64, "pp_propname": STR, "pp_value": BIN},
    ),
    "redirect": (
        ["rd_from", "rd_namespace", "rd_title", "rd_interwiki", "rd_fragment"],
        {"rd_from": I64, "rd_namespace": I32, "rd_title": BIN},
    ),
}

# page_props has many properties; keep only the ones the plan uses.
KEEP_PROPS = ["wikibase_item", "hiddencat", "disambiguation", "wikibase-shortdesc"]

BLOCK_BYTES = 32 * 1024 * 1024


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in SPEC:
        sys.exit("usage: ... | sqldump_to_parquet.py {%s} OUT.parquet" % "|".join(SPEC))
    table, out = sys.argv[1], sys.argv[2]
    cols, types = SPEC[table]
    skipped = 0

    def bad_row(_row):
        nonlocal skipped
        skipped += 1
        return "skip"

    reader = pc.open_csv(
        sys.stdin.buffer,
        read_options=pc.ReadOptions(column_names=cols, block_size=BLOCK_BYTES),
        parse_options=pc.ParseOptions(
            quote_char="'", double_quote=False, escape_char="\\",
            newlines_in_values=False, invalid_row_handler=bad_row,
        ),
        convert_options=pc.ConvertOptions(
            column_types=types, include_columns=list(types),
            null_values=["NULL"], strings_can_be_null=True, quoted_strings_can_be_null=False,
        ),
    )

    tmp = out + ".partial"
    writer = None
    rows = 0
    t0 = time.time()
    for batch in reader:
        t = pa.Table.from_batches([batch])
        if table == "page_props":
            t = t.filter(pcomp.is_in(t["pp_propname"], value_set=pa.array(KEEP_PROPS)))
        if writer is None:
            writer = pq.ParquetWriter(tmp, t.schema, compression="zstd")
        writer.write_table(t)
        rows += t.num_rows
    if writer is None:
        sys.exit("no rows parsed")
    writer.close()
    os.replace(tmp, out)
    print(json.dumps({"table": table, "rows_kept": rows, "rows_skipped": skipped,
                      "seconds": round(time.time() - t0, 1), "out": out}))


if __name__ == "__main__":
    main()
