# WikiData.x3d (repository: wikigrokker)

A pipeline that maps English Wikipedia into a two-facet hierarchy:

- a **type tree** from Wikidata classes (instance of P31, subclass of P279), and
- a **topic tree** from the Vital Articles scaffold plus the cleaned category graph,

then folds both for display. Plan, design and the ten QA checks live in the planning doc:
<https://claude.ai/artifact/KSJsgQcFHoziFaVavufP3z>

## Status

| Step | State |
| --- | --- |
| 0. Spike: data access, dump layout, scale | done |
| 1. Dev slice (Vital Articles levels 1 to 5) and first QA report | done |
| 2. Type spine (P31 + P279 for all articles) | not started |
| 3. Topic spine (categories projected onto the Vital scaffold) | not started |
| 4. Reconcile, fold, run all ten checks on the full slice | not started |
| 5. Export contract and one test viewer | not started |

Data snapshot: English Wikipedia dump of 2026-10-01. For all six SQL tables used, the `latest` file and the
`20261001` file have identical size and timestamp, so everything comes from one snapshot. Wikidata comes from the
public QLever endpoint at its index date (not recorded; a few recent pages have no item).

## Requirements

Python 3.11+ with `pyarrow`, `duckdb`, `numpy`, `scipy`, `networkx`; `curl`, `gzip`, `grep`, `sed`.
Allowlisted hosts: `cdimage.debian.org` (dump mirror), `dumps.wikimedia.org` (only for `run_table.sh`), `qlever.dev`.
About 4 GB of disk for the Parquet tables, plus up to 2.6 GB while one dump file is being converted.
Python is always run as `python3 -I` because the inputs are downloaded data.

## Run order

```bash
# 1. SQL tables to Parquet: resumable download, gzip -t check, streamed conversion (data/parquet/, 3.5 GB)
for t in category page page_props redirect linktarget categorylinks; do
  scripts/run_table_mirror.sh "$t" 20261001
done

# 2. Wikidata exports from QLever (see "Known gaps"), then Q-id Parquet
scripts/qlever.sh "$QUERY" tsv 600 data/qlever/NAME.tsv
python3 -I scripts/qlever_to_parquet.py data/qlever data/qlever_parquet
python3 -I scripts/analyze_class_graph.py data/qlever/class_edges.tsv data/qlever/class_labels.tsv \
        data/qlever/type_counts.tsv out/class_graph_stats.json

# 3. Vital Articles: find the dump blocks, fetch only those pages, parse
python3 -I scripts/vital_ranges.py data/index/enwiki-20261001-pages-articles-multistream-index.txt.bz2 \
        "Wikipedia:Vital articles" out/vital
python3 -I scripts/extract_dump_pages.py out/vital/ranges.txt out/vital/titles.txt \
        https://cdimage.debian.org/mirror/wikimedia.org/dumps/enwiki/20261001/enwiki-20261001-pages-articles-multistream.xml.bz2 \
        out/vital_pages.jsonl
python3 -I scripts/parse_vital.py out/vital_pages.jsonl data/vital_parsed

# 4. Dev slice, category graph, agreement tests (each writes a new directory and a JSON report)
python3 -I scripts/build_dev_slice.py data/vital_parsed data/parquet data/qlever_parquet data/dev_slice
python3 -I scripts/category_graph_stats.py data/parquet data/catgraph
python3 -I scripts/topic_crosstab.py data/catgraph data/dev_slice data/qlever_parquet out/topic_crosstab
python3 -I scripts/loo_topic_baseline.py data/dev_slice out/loo_topic_baseline.json
```

The multistream index (285 MB) is fetched once from the same mirror directory as the dump.

## What each script does

| Script | Purpose |
| --- | --- |
| `run_table_mirror.sh` | Download one SQL dump with resume, test it with `gzip -t`, convert it, delete the `.gz` |
| `run_table.sh` | Same conversion streamed straight from the network; a dropped connection truncates silently, so prefer the mirror script |
| `sqldump_to_parquet.py` | Streaming parser for the one-row-per-line dump format; keeps needed columns, titles as raw bytes, zstd Parquet |
| `qlever.sh` | One SPARQL query to QLever with prefixes added and backoff on 429/503 |
| `qlever_to_parquet.py` | QLever TSV exports to Parquet with bare Q-ids; drops blank nodes and unknown-value rows |
| `analyze_class_graph.py` | Wikidata class graph: cycles, roots, depth, parents per class |
| `vital_ranges.py` | Byte ranges of the multistream blocks that hold pages with a title prefix |
| `extract_dump_pages.py` | Fetch those blocks with HTTP Range requests and write the wanted pages as JSONL |
| `parse_vital.py` | Vital Articles JSON data pages and level-list pages to Parquet (placement, headings, nested lists) |
| `build_dev_slice.py` | Title to page id (following redirects) to Wikidata item, P31 types and categories |
| `category_graph_stats.py` | Category graph size, cycles, parents, depth from Main topic classifications, article coverage |
| `topic_crosstab.py` | Vital topics against the category tree's top level (three scorers, NMI, purity) |
| `loo_topic_baseline.py` | Leave-one-out prediction of a Vital topic from the categories of the other Vital articles |

## Measured results

Full numbers are in `results/`. Headlines:

| Measure | Result |
| --- | --- |
| Tables | page 66,384,916 rows; categorylinks 226,560,276; linktarget 131,511,582; redirect 15,551,857; category 2,648,976 |
| Articles | 7,249,474 (99.92% with a Wikidata item, 95.31% with a P31 type) |
| Category graph | 10,055,309 sub-category links; 84.7% of visible categories have 2+ parents; 685 visible categories in 174 cycles |
| Main topic classifications | 34 children; reaches 89.3% of visible categories and 96.1% of articles |
| Vital Articles | 49,872 titles, all resolved; P31 typed 86.7%, 99.58% with P279 |
| Topic agreement | category tops vs Vital topics NMI 0.26; leave-one-out topic prediction 92.5% on 97.8% of articles (majority class 28.4%) |
| Repeatability | dev slice rebuild is row-identical; builds in 27 s |

## Known gaps

- The SPARQL texts behind `class_edges`, `class_labels`, `type_counts` and `item_types` were run interactively and not saved.
  They will be recreated, and checked against the committed counts, when step 2 extends the export.
  `queries/untyped_p279.rq.template` is the one query kept as a file.
- 500 of the 6,411 untyped Vital items point to subclass parents outside the current class export.
- Some visible maintenance categories (for example "Possibly free images") are not flagged hidden and need explicit cleaning in step 3.
- Wikipedia text is CC BY-SA and Wikidata is CC0. This repo stores IDs, counts and structure, not article text.
