#!/usr/bin/env python3
"""Dev slice (roadmap step 1): the Vital Articles (levels 1-5) resolved to enwiki page ids,
Wikidata items, P31 types and categories.

usage: python3 -I build_dev_slice.py VITAL_PARSED_DIR ENWIKI_PARQUET_DIR QLEVER_PARQUET_DIR OUT_DIR

Inputs   VITAL_PARSED_DIR   output of parse_vital.py
         ENWIKI_PARQUET_DIR page, redirect, page_props, linktarget, categorylinks (.parquet)
         QLEVER_PARQUET_DIR item_types.parquet (from qlever_to_parquet.py)
Outputs  OUT_DIR/articles.parquet            one row per Vital title (placement + page id + qid + counts)
         OUT_DIR/article_types.parquet       (qid, type)           P31 values
         OUT_DIR/article_categories.parquet  (page_id, category, hidden)
         OUT_DIR/report.json                 resolution / coverage numbers
"""
import json
import re
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

INVIS = re.compile("[‎‏‪-‮⁦-⁩﻿]")


def clean(t):
    t = INVIS.sub("", t).replace("_", " ").strip()
    t = re.sub(r"\s+", " ", t)
    return t[:1].upper() + t[1:]


def main(vp, wp, qp, out):
    vp, wp, qp, out = Path(vp), Path(wp), Path(qp), Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "tmp").mkdir()
    t00 = time.time()

    def log(msg):
        print(f"[{time.time() - t00:6.1f}s] {msg}", flush=True)

    # ---- the Vital title table (JSON placement + titles only seen in the wikitext lists)
    va = {}
    for r in pq.read_table(vp / "vital_json.parquet").to_pylist():
        t = clean(r["title"])
        va[t] = dict(title=t, level=r["level"], topic=r["topic"], section=r["section"],
                     sublist=r["sublist"], in_json=True, in_tree=False)
    tree_level = {}
    for r in pq.read_table(vp / "vital_tree.parquet", columns=["title", "page_level", "level_note"]).to_pylist():
        t = clean(r["title"])
        lv = r["level_note"] or r["page_level"]
        tree_level[t] = min(lv, tree_level.get(t, 9))
    for t, lv in tree_level.items():
        if t in va:
            va[t]["in_tree"] = True
        else:
            va[t] = dict(title=t, level=lv, topic=None, section=None, sublist=None, in_json=False, in_tree=True)
    schema = pa.schema([("title", pa.string()), ("level", pa.int32()), ("topic", pa.string()),
                        ("section", pa.string()), ("sublist", pa.string()),
                        ("in_json", pa.bool_()), ("in_tree", pa.bool_())])
    va_raw = pa.Table.from_pylist(list(va.values()), schema=schema)
    log(f"{len(va)} Vital titles ({sum(1 for v in va.values() if v['in_json'])} in JSON, "
        f"{sum(1 for v in va.values() if v['in_tree'])} in the list pages)")

    con = duckdb.connect()
    con.execute(f"pragma threads=2; pragma memory_limit='4GB'; pragma temp_directory='{out / 'tmp'}'")
    con.register("va_raw", va_raw)
    P = lambda n: f"read_parquet('{wp}/{n}.parquet')"

    con.execute("create table va as select *, encode(replace(title, ' ', '_')) as tkey from va_raw")

    # ---- 1. title -> page id (namespace 0), then follow redirects
    con.execute(f"""create table res as
        select v.title, p.page_id as orig_id, p.page_id as cur_id, p.page_is_redirect as cur_redirect, 0 as hops
        from va v join {P('page')} p on p.page_namespace = 0 and p.page_title = v.tkey""")
    log(f"matched {con.execute('select count(*) from res').fetchone()[0]} titles to pages")
    for hop in range(1, 6):
        n = con.execute("select count(*) from res where cur_redirect = 1").fetchone()[0]
        if n == 0:
            break
        log(f"hop {hop}: {n} titles are redirects")
        con.execute(f"""create or replace table rdset as
            select rd.rd_from, rd.rd_namespace, rd.rd_title from {P('redirect')} rd
            join (select distinct cur_id from res where cur_redirect = 1) c on rd.rd_from = c.cur_id""")
        con.execute(f"""create or replace table tpset as
            select p.page_id, p.page_namespace, p.page_title, p.page_is_redirect from {P('page')} p
            join (select distinct rd_namespace, rd_title from rdset) t
              on p.page_namespace = t.rd_namespace and p.page_title = t.rd_title""")
        con.execute("""create or replace table res as
            select r.title, r.orig_id,
                   coalesce(tp.page_id, r.cur_id) as cur_id,
                   coalesce(tp.page_is_redirect, r.cur_redirect) as cur_redirect,
                   r.hops + case when tp.page_id is not null then 1 else 0 end as hops
            from res r
            left join rdset rd on r.cur_redirect = 1 and rd.rd_from = r.cur_id
            left join tpset tp on rd.rd_namespace = tp.page_namespace and rd.rd_title = tp.page_title""")
    con.execute("""create or replace table res as
        select title, orig_id, case when cur_redirect = 1 then null else cur_id end as cur_id, hops from res""")

    # ---- 2. resolved title, Wikidata item
    con.execute(f"""create table rtitle as
        select p.page_id, replace(decode(p.page_title), '_', ' ') as resolved_title
        from {P('page')} p join (select distinct cur_id from res where cur_id is not null) c on p.page_id = c.cur_id""")
    con.execute(f"""create table qid as
        select pp_page as page_id, decode(pp_value) as qid from {P('page_props')}
        where pp_propname = 'wikibase_item' and pp_page in (select distinct cur_id from res where cur_id is not null)""")
    con.execute("""create table articles0 as
        select v.title, v.level, v.topic, v.section, v.sublist, v.in_json, v.in_tree,
               r.cur_id as page_id, r.hops, rt.resolved_title, q.qid
        from va v left join res r on r.title = v.title
                  left join rtitle rt on rt.page_id = r.cur_id
                  left join qid q on q.page_id = r.cur_id""")
    log("resolved page ids and Wikidata items")

    # ---- 3. P31 types
    con.execute(f"""create table atypes as
        select distinct a.qid, t.type from articles0 a
        join read_parquet('{qp}/item_types.parquet') t on t.item = a.qid where a.qid is not null""")
    log(f"{con.execute('select count(*) from atypes').fetchone()[0]} (item, type) pairs")

    # ---- 4. categories (visible vs hidden)
    con.execute(f"""create table hid as
        select p.page_title as cat_key from {P('page')} p
        where p.page_namespace = 14
          and p.page_id in (select pp_page from {P('page_props')} where pp_propname = 'hiddencat')""")
    con.execute(f"""create table va_cl as
        select cl.cl_from as page_id, cl.cl_target_id
        from {P('categorylinks')} cl
        where cl.cl_type = 'page' and cl.cl_from in (select distinct page_id from articles0 where page_id is not null)""")
    con.execute(f"""create table acats as
        select c.page_id, replace(decode(lt.lt_title), '_', ' ') as category, (h.cat_key is not null) as hidden
        from va_cl c join {P('linktarget')} lt on lt.lt_id = c.cl_target_id and lt.lt_namespace = 14
        left join hid h on h.cat_key = lt.lt_title""")
    log(f"{con.execute('select count(*) from acats').fetchone()[0]} (article, category) pairs")

    con.execute("""create table articles as
        select a.*,
               coalesce(ty.n_types, 0) as n_types,
               coalesce(ca.n_visible, 0) as n_visible_cats,
               coalesce(ca.n_hidden, 0) as n_hidden_cats
        from articles0 a
        left join (select qid, count(*) as n_types from atypes group by 1) ty on ty.qid = a.qid
        left join (select page_id, sum(case when hidden then 0 else 1 end) as n_visible,
                          sum(case when hidden then 1 else 0 end) as n_hidden from acats group by 1) ca
               on ca.page_id = a.page_id""")

    for name in ["articles", "atypes", "acats"]:
        dest = {"articles": "articles", "atypes": "article_types", "acats": "article_categories"}[name]
        con.execute(f"copy {name} to '{out}/{dest}.parquet' (format parquet, compression zstd)")

    # ---- report
    q = lambda s: con.execute(s).fetchone()[0]
    rep = {
        "vital_titles": q("select count(*) from articles"),
        "resolved_to_page": q("select count(*) from articles where page_id is not null"),
        "followed_redirect": q("select count(*) from articles where hops > 0 and page_id is not null"),
        "unresolved": q("select count(*) from articles where page_id is null"),
        "distinct_pages": q("select count(distinct page_id) from articles"),
        "with_qid": q("select count(*) from articles where qid is not null"),
        "with_p31_type": q("select count(*) from articles where n_types > 0"),
        "distinct_types_used": q("select count(distinct type) from atypes"),
        "with_visible_category": q("select count(*) from articles where n_visible_cats > 0"),
        "distinct_visible_categories": q("select count(distinct category) from acats where not hidden"),
        "distinct_hidden_categories": q("select count(distinct category) from acats where hidden"),
        "visible_cats_per_article": dict(zip(["mean", "median", "p90", "max"], con.execute(
            "select round(avg(n_visible_cats), 2), median(n_visible_cats), quantile_cont(n_visible_cats, 0.9), max(n_visible_cats) "
            "from articles where page_id is not null").fetchone())),
        "unresolved_titles": [r[0] for r in con.execute("select title from articles where page_id is null order by 1 limit 60").fetchall()],
        "no_qid_titles": [r[0] for r in con.execute("select title from articles where page_id is not null and qid is null order by 1 limit 40").fetchall()],
        "by_level": con.execute("""select level, count(*) as n, sum((page_id is not null)::int) as page, sum((qid is not null)::int) as qid,
                                           sum((n_types > 0)::int) as typed, sum((n_visible_cats > 0)::int) as categorised
                                    from articles group by 1 order by 1""").fetchall(),
        "by_topic": con.execute("""select coalesce(topic, '(none)') as topic, count(*) as n, sum((qid is not null)::int) as qid,
                                           sum((n_types > 0)::int) as typed, sum((n_visible_cats > 0)::int) as categorised,
                                           round(avg(n_visible_cats), 1) as cats_per_article
                                    from articles group by 1 order by 2 desc""").fetchall(),
    }
    (out / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    log("done")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
