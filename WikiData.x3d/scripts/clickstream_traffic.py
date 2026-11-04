#!/usr/bin/env python3
"""Reader traffic from the monthly English Wikipedia clickstream.

usage: clickstream_traffic.py CLICKSTREAM.tsv.gz ENWIKI_PARQUET_DIR OUT_DIR

Reads (prev, curr, type, n) rows, resolves titles to article page ids (following
one redirect hop), and writes:
  article_traffic.parquet  page_id, views_in (sum of n over rows whose curr is the article),
                           from_search, from_external, from_links
  link_flows.parquet       src_page_id, dst_page_id, n  (type = 'link', both ends resolved)
  report.json              row counts, sums and resolution rates

The output directory is written as OUT_DIR.tmp and renamed only after every
check passes (load gate).

What a pass does not establish: clickstream drops (prev, curr) pairs seen 10 times
or fewer, so views_in undercounts the long tail; it is not the pageview count.
"""
import json
import os
import shutil
import sys
import time

import duckdb

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "loom"))
from loomlog import log  # noqa: E402


def main(src, pq_dir, out_dir):
    t0 = time.time()
    tmp = out_dir + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    con = duckdb.connect()
    con.execute("SET memory_limit='5GB'; SET threads=2; SET preserve_insertion_order=false")
    con.execute(f"""
        CREATE TABLE cs AS SELECT * FROM read_csv('{src}', delim='\t', header=false, quote='', escape='',
            columns={{'prev':'VARCHAR','curr':'VARCHAR','type':'VARCHAR','n':'BIGINT'}})""")
    rows, n_sum = con.execute("SELECT count(*), sum(n) FROM cs").fetchone()
    by_type = dict(con.execute("SELECT type, count(*) FROM cs GROUP BY 1").fetchall())

    # Title -> article page id, following one redirect hop to a main-namespace page.
    con.execute(f"""
        CREATE TABLE titles AS
        WITH p AS (SELECT page_id, decode(page_title) AS page_title, page_is_redirect FROM '{pq_dir}/page.parquet' WHERE page_namespace = 0),
        r AS (SELECT rd_from, decode(rd_title) AS rd_title FROM '{pq_dir}/redirect.parquet' WHERE rd_namespace = 0)
        SELECT p.page_title AS title,
               CASE WHEN p.page_is_redirect = 1 THEN t.page_id ELSE p.page_id END AS page_id
        FROM p
        LEFT JOIN r ON p.page_is_redirect = 1 AND r.rd_from = p.page_id
        LEFT JOIN p t ON t.page_title = r.rd_title AND t.page_is_redirect = 0""")
    con.execute("CREATE TABLE ids AS SELECT DISTINCT title, page_id FROM titles WHERE page_id IS NOT NULL")
    dup = con.execute("SELECT count(*) FROM (SELECT title FROM ids GROUP BY 1 HAVING count(*) > 1)").fetchone()[0]

    con.execute("""
        CREATE TABLE csr AS SELECT cs.*, c.page_id AS dst, p.page_id AS src
        FROM cs LEFT JOIN ids c ON c.title = cs.curr LEFT JOIN ids p ON p.title = cs.prev""")
    curr_rows_ok, curr_n_ok = con.execute("SELECT count(*), sum(n) FROM csr WHERE dst IS NOT NULL").fetchone()
    con.execute(f"""
        COPY (SELECT dst AS page_id, sum(n) AS views_in,
                     sum(CASE WHEN prev = 'other-search' THEN n ELSE 0 END) AS from_search,
                     sum(CASE WHEN prev IN ('other-external','other-empty','other-other') THEN n ELSE 0 END) AS from_external,
                     sum(CASE WHEN type = 'link' THEN n ELSE 0 END) AS from_links
              FROM csr WHERE dst IS NOT NULL GROUP BY 1 ORDER BY 1)
        TO '{tmp}/article_traffic.parquet' (FORMAT parquet, COMPRESSION zstd)""")
    con.execute(f"""
        COPY (SELECT src AS src_page_id, dst AS dst_page_id, sum(n) AS n FROM csr
              WHERE type = 'link' AND src IS NOT NULL AND dst IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2)
        TO '{tmp}/link_flows.parquet' (FORMAT parquet, COMPRESSION zstd)""")
    art_rows, art_sum = con.execute(f"SELECT count(*), sum(views_in) FROM '{tmp}/article_traffic.parquet'").fetchone()
    link_rows_all, link_n_all = con.execute("SELECT count(*), sum(n) FROM csr WHERE type = 'link'").fetchone()
    lf_rows, lf_sum = con.execute(f"SELECT count(*), sum(n) FROM '{tmp}/link_flows.parquet'").fetchone()
    top = con.execute(f"""SELECT decode(p.page_title), a.views_in FROM '{tmp}/article_traffic.parquet' a
                          JOIN '{pq_dir}/page.parquet' p ON p.page_id = a.page_id
                          ORDER BY a.views_in DESC LIMIT 12""").fetchall()

    checks = {
        "every row parsed into 4 typed fields": rows > 0 and con.execute("SELECT count(*) FROM cs WHERE n IS NULL OR curr IS NULL").fetchone()[0] == 0,
        "views_in sums back to resolved rows (conservation)": art_sum == curr_n_ok,
        "curr resolves for at least 99% of clicks": curr_n_ok / n_sum >= 0.99,
        "no title maps to two page ids": dup == 0,
    }
    report = {
        "source": os.path.basename(src),
        "rows": rows, "clicks": n_sum, "rows_by_type": by_type,
        "curr_resolved_rows": curr_rows_ok, "curr_resolved_clicks": curr_n_ok,
        "curr_resolved_click_share": round(curr_n_ok / n_sum, 5),
        "articles_with_traffic": art_rows, "views_in_sum": art_sum,
        "link_rows": link_rows_all, "link_clicks": link_n_all,
        "link_flows_both_resolved": lf_rows, "link_flow_clicks": lf_sum,
        "titles_with_two_ids": dup,
        "top_articles": top,
        "checks": checks,
        "not_established": ["clickstream omits pairs with 10 or fewer clicks, so long-tail articles are undercounted",
                            "views_in is not the pageview count; it counts arrivals recorded in clickstream"],
        "seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(tmp, "report.json"), "w") as f:
        json.dump(report, f, indent=1)
    if not all(checks.values()):
        log("verdict", "clickstream load gate FAILED; output left in .tmp", mark="C", checks=checks)
        print(json.dumps(report, indent=1))
        sys.exit(1)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.rename(tmp, out_dir)
    log("build", f"clickstream traffic: {rows:,} rows, {n_sum:,} clicks, {curr_n_ok / n_sum:.2%} resolved to articles; "
        f"{art_rows:,} articles with traffic; {lf_rows:,} article-to-article link flows", mark="C",
        checks=checks, out=out_dir)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
