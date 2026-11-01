#!/usr/bin/env bash
# Prefer run_table_mirror.sh: a dropped connection while streaming silently truncates the table.
# Stream one enwiki SQL dump into Parquet without storing the .gz on disk.
# usage: run_table.sh TABLE            (downloads from dumps.wikimedia.org)
#        run_table.sh TABLE FILE.gz    (reads a local file)
set -euo pipefail
TABLE="$1"
SRC="${2:-}"
ROOT="${WIKIGROKKER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OUT="$ROOT/data/parquet/$TABLE.parquet"
UA='wiki-map-iteration0/0.1 (https://claude.ai; research planning)'
URL="https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-$TABLE.sql.gz"

if [ -n "$SRC" ]; then
  cat "$SRC"
else
  curl -sS -L --retry 3 -A "$UA" "$URL"
fi \
  | LC_ALL=C gzip -dc \
  | LC_ALL=C grep -a '^(' \
  | LC_ALL=C sed -E 's/^\(//; s/\)[,;]$//' \
  | python3 -I "$ROOT/scripts/sqldump_to_parquet.py" "$TABLE" "$OUT"
