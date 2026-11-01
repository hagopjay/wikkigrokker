#!/usr/bin/env bash
# Download one enwiki SQL dump from the Debian mirror (resumable, verified), then convert it to Parquet.
# usage: run_table_mirror.sh TABLE [DUMP_DATE]     e.g. run_table_mirror.sh categorylinks 20261001
#
# Why not stream straight from the network: a dropped connection mid-stream silently truncates the
# table. Here the file is fetched with resume, tested with gzip -t, and only then parsed.
set -euo pipefail
TABLE="$1"
DATE="${2:-20261001}"
ROOT="${WIKIGROKKER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DL="$ROOT/data/dumps/$TABLE"
F="$DL/enwiki-$DATE-$TABLE.sql.gz"
OUT="$ROOT/data/parquet/$TABLE.parquet"
UA='wiki-map-iteration0/0.1 (https://claude.ai; research planning)'
URL="https://cdimage.debian.org/mirror/wikimedia.org/dumps/enwiki/$DATE/enwiki-$DATE-$TABLE.sql.gz"
mkdir -p "$DL"

ok=0
for attempt in 1 2 3 4 5 6 7 8; do
  if curl -sS -L -C - --retry 3 -A "$UA" -o "$F" "$URL"; then ok=1; break; fi
  echo "download attempt $attempt failed, resuming" >&2
  sleep 5
done
[ "$ok" = 1 ] || { echo "download failed for $TABLE" >&2; exit 1; }

gzip -t "$F"   # exits non-zero if the file is truncated or corrupt
echo "downloaded and verified: $(stat -c %s "$F") bytes"

LC_ALL=C gzip -dc "$F" \
  | LC_ALL=C grep -a '^(' \
  | LC_ALL=C sed -E 's/^\(//; s/\)[,;]$//' \
  | python3 -I "$ROOT/scripts/sqldump_to_parquet.py" "$TABLE" "$OUT"
rm -f "$F"   # the Parquet file is the product; the dump can be fetched again
