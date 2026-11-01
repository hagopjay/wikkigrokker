#!/usr/bin/env bash
# Run one SPARQL query against the public QLever Wikidata index.
# usage: qlever.sh 'SELECT ...' [json|tsv|csv] [timeout_seconds] [OUTFILE]
# Prints the response (or writes OUTFILE). Retries with backoff on HTTP 429/503.
# Common prefixes are added automatically (QLever has no default prefixes).
set -uo pipefail
Q="$1"; FMT="${2:-json}"; TMO="${3:-300}"; OUT="${4:-}"
case "$FMT" in
  json) ACC='application/sparql-results+json' ;;
  tsv)  ACC='text/tab-separated-values' ;;
  csv)  ACC='text/csv' ;;
  *) echo "format must be json, tsv or csv" >&2; exit 2 ;;
esac
P='PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> PREFIX schema: <http://schema.org/> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> PREFIX wikibase: <http://wikiba.se/ontology#> '
TMP="$(mktemp /tmp/qlever.XXXXXX)"
trap 'rm -f "$TMP"' EXIT
for attempt in 1 2 3 4 5 6 7 8; do
  code=$(curl -sS -m "$TMO" -A 'wiki-map-iteration0/0.1 (https://claude.ai; research planning)' \
    -H "Accept: $ACC" --data-urlencode "query=$P$Q" -o "$TMP" -w '%{http_code}' https://qlever.dev/api/wikidata) || code=000
  if [ "$code" = 200 ]; then
    if [ -n "$OUT" ]; then mv "$TMP" "$OUT"; trap - EXIT; else cat "$TMP"; fi
    exit 0
  fi
  if [ "$code" = 429 ] || [ "$code" = 503 ] || [ "$code" = 000 ]; then
    sleep $((8 * attempt)); continue
  fi
  echo "HTTP $code" >&2; head -c 800 "$TMP" >&2; exit 1
done
echo "gave up after repeated 429/503" >&2
exit 1
