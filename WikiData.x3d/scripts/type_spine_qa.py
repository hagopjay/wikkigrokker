#!/usr/bin/env python3
"""QA for the type spine (step 2).

usage: python3 -I type_spine_qa.py RUN_A RUN_B DEV_SLICE_DIR ENWIKI_PARQUET_DIR OUT_DIR

RUN_A, RUN_B        two output directories of build_type_spine.py built from the same inputs
DEV_SLICE_DIR       output of build_dev_slice.py (Vital Articles with topics)
ENWIKI_PARQUET_DIR  page.parquet (titles for the sample)
OUT_DIR             new directory: qa.json, sample_paths.csv

Checks
  determinism   the three result tables of RUN_A and RUN_B differ in no row
  vital         Vital topic against the type tree's top-level class (counts, agreement score NMI)
  depth         depth in the primary tree of the class each article is shown under (folded view)
  sample        60 articles chosen by a hash of the page id, with their full type path
"""
import csv
import json
import sys
from pathlib import Path

import duckdb
import numpy as np


def nmi(M):
    P = M / M.sum()
    pi, pj = P.sum(axis=1, keepdims=True), P.sum(axis=0, keepdims=True)
    nz = P > 0
    mi = float((P[nz] * np.log(P[nz] / (pi @ pj)[nz])).sum())
    hi = float(-(pi[pi > 0] * np.log(pi[pi > 0])).sum())
    hj = float(-(pj[pj > 0] * np.log(pj[pj > 0])).sum())
    return mi / np.sqrt(hi * hj) if hi > 0 and hj > 0 else 0.0


def main(a, b, ds, wp, out):
    a, b, ds, wp, out = map(Path, (a, b, ds, wp, out))
    out.mkdir(parents=True, exist_ok=False)
    con = duckdb.connect()
    con.execute("pragma threads=2; pragma memory_limit='4GB'")
    qa = {}

    # ---- determinism
    det = {}
    for t in ("classes", "edges", "article_type"):
        fa, fb = f"read_parquet('{a}/{t}.parquet')", f"read_parquet('{b}/{t}.parquet')"
        d1 = con.execute(f"select count(*) from (select * from {fa} except select * from {fb})").fetchall()[0][0]
        d2 = con.execute(f"select count(*) from (select * from {fb} except select * from {fa})").fetchall()[0][0]
        n = con.execute(f"select count(*) from {fa}").fetchall()[0][0]
        det[t] = {"rows": n, "only_in_a": d1, "only_in_b": d2}
    qa["determinism"] = det

    # ---- tree helpers
    cl = con.execute(f"select id, name, label, parent, depth, primary_count, folded from read_parquet('{a}/classes.parquet')").fetchall()
    info = {r[0]: r for r in cl}
    thing = next(r[0] for r in cl if r[1] == "schema:Thing")

    def top_of(v):
        while v != thing and info[v][3] != thing:
            v = info[v][3]
        return v

    top = {v: (v if v == thing else top_of(v)) for v in info}
    tops = sorted({top[v] for v in info if v != thing})
    top_name = {t: info[t][1] for t in tops}

    def path(v):
        out_ = []
        while True:
            out_.append(f"{info[v][2]}")
            if v == thing:
                break
            v = info[v][3]
        return " > ".join(reversed(out_))

    # ---- Vital topics against the top-level class
    import pyarrow as pa
    con.register("top_t", pa.table({"id": pa.array(list(top), pa.int64()), "top": pa.array(list(top.values()), pa.int64())}))
    rows = con.execute(f"""
        select v.topic, t.top, count(*) from read_parquet('{ds}/articles.parquet') v
        join read_parquet('{a}/article_type.parquet') x on x.page_id = v.page_id
        join top_t t on t.id = x.primary_type
        where v.topic is not null group by 1, 2""").fetchall()
    topics = sorted({r[0] for r in rows})
    M = np.zeros((len(topics), len(tops)))
    for tp, tt, n in rows:
        M[topics.index(tp), tops.index(tt)] = n
    vit = {"articles": int(M.sum()), "nmi": round(nmi(M), 4), "by_topic": {}}
    for i, tp in enumerate(topics):
        o = np.argsort(-M[i])[:3]
        vit["by_topic"][tp] = {"n": int(M[i].sum()), "top": [(top_name[tops[j]], round(float(M[i, j] / M[i].sum()), 3)) for j in o if M[i, j] > 0]}
    vit["by_top_class"] = {}
    for j, t in enumerate(tops):
        if M[:, j].sum():
            o = np.argsort(-M[:, j])[:3]
            vit["by_top_class"][top_name[t]] = {"n": int(M[:, j].sum()), "top": [(topics[i], round(float(M[i, j] / M[:, j].sum()), 3)) for i in o if M[i, j] > 0]}
    qa["vital_topic_vs_type"] = vit

    # ---- depth of the class each article is shown under
    dd = con.execute(f"select display_class, count(*) from read_parquet('{a}/article_type.parquet') where display_class is not null group by 1").fetchall()
    dep = np.repeat([info[v][4] for v, _ in dd], [n for _, n in dd])
    qa["display_depth"] = {"articles": int(len(dep)), "mean": round(float(dep.mean()), 2), "median": float(np.median(dep)),
                           "p90": float(np.percentile(dep, 90)), "max": int(dep.max()),
                           "distinct_classes_shown": len(dd)}
    pdd = con.execute(f"select primary_type, count(*) from read_parquet('{a}/article_type.parquet') where primary_type is not null group by 1").fetchall()
    pdep = np.repeat([info[v][4] for v, _ in pdd], [n for _, n in pdd])
    qa["primary_depth"] = {"mean": round(float(pdep.mean()), 2), "median": float(np.median(pdep)), "p90": float(np.percentile(pdep, 90)), "max": int(pdep.max())}
    shown = [r for r in cl if not r[6]]
    qa["largest_shown_classes"] = [(r[2], r[1], int(r[5])) for r in sorted(shown, key=lambda r: -r[5]) if r[0] != thing][:25]

    # ---- sample of real paths
    smp = con.execute(f"""
        select x.page_id, replace(decode(p.page_title), '_', ' ') as title, x.primary_type, x.display_class, x.source
        from read_parquet('{a}/article_type.parquet') x
        join read_parquet('{wp}/page.parquet') p on p.page_id = x.page_id and p.page_namespace = 0
        where x.primary_type is not null order by hash(x.page_id) limit 60""").fetchall()
    with open(out / "sample_paths.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["title", "source", "primary_type_path", "shown_as"])
        for _, title, pt, dc, src in smp:
            w.writerow([title, src, path(pt), info[dc][2]])

    (out / "qa.json").write_text(json.dumps(qa, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in qa.items() if k != "largest_shown_classes"}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 6:
        sys.exit(__doc__)
    main(*sys.argv[1:])
