#!/usr/bin/env python3
"""Gold agreement, first look: how do the Vital Articles' 11 topics line up with the category tree's
top level (the 34 children of Category:Main topic classifications), and what do the Wikidata types say?

usage: python3 -I topic_crosstab.py CATGRAPH_DIR DEV_SLICE_DIR QLEVER_PARQUET_DIR OUT_DIR

Three ways to give an article a category-side topic (reachability is downward over visible sub-category edges):
  share    top-level topic above the largest share of the article's visible categories
  nearest  top-level topic with the smallest distance to any of the article's categories
           (ties: larger share)  - the plan's 'closer to the scaffold root wins' rule
  topical  like share, after dropping the non-topical tops (Academic disciplines, Concepts, Entities,
           Lists, Main topic articles)
Scores: purity both ways and normalised mutual information (NMI) against the Vital topic.
"""
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

NON_TOPICAL = {"Academic disciplines", "Concepts", "Entities", "Lists", "Main topic articles"}


def nmi(M):
    P = M / M.sum()
    pi, pj = P.sum(axis=1, keepdims=True), P.sum(axis=0, keepdims=True)
    nz = P > 0
    mi = float((P[nz] * np.log(P[nz] / (pi @ pj)[nz])).sum())
    hi = float(-(pi[pi > 0] * np.log(pi[pi > 0])).sum())
    hj = float(-(pj[pj > 0] * np.log(pj[pj > 0])).sum())
    return mi / np.sqrt(hi * hj) if hi > 0 and hj > 0 else 0.0


def main(cg, ds, qp, out):
    cg, ds, qp, out = Path(cg), Path(ds), Path(qp), Path(out)
    out.mkdir(parents=True, exist_ok=False)
    t0 = time.time()
    log = lambda m: print(f"[{time.time() - t0:6.1f}s] {m}", flush=True)
    con = duckdb.connect()
    con.execute("pragma threads=2; pragma memory_limit='4GB'")

    nodes = con.execute(f"select idx, title, hidden from read_parquet('{cg}/nodes.parquet') order by idx").fetchnumpy()
    n = len(nodes["idx"])
    hidden = nodes["hidden"].astype(bool)
    titles = nodes["title"]
    e = con.execute(f"select child, parent from read_parquet('{cg}/edges.parquet')").fetchnumpy()
    c, p = e["child"], e["parent"]
    keep = ~hidden[c] & ~hidden[p] & (c != p)
    A = csr_matrix((np.ones(int(keep.sum()), dtype=np.int8), (p[keep], c[keep])), shape=(n, n))
    src = int(np.where(titles == "Main topic classifications")[0][0])
    tops = A[src].indices
    tops = tops[np.argsort([str(titles[i]) for i in tops])]
    top_names = [str(titles[i]) for i in tops]
    T = len(tops)
    log(f"{T} top-level topics")

    D = np.full((n, T), -1, dtype=np.int8)
    for j, t in enumerate(tops):
        d = dijkstra(A, directed=True, indices=int(t), unweighted=True)
        ok = np.isfinite(d)
        D[ok, j] = np.minimum(d[ok], 100).astype(np.int8)
    log("distances from each top-level topic computed")

    arts = con.execute(f"""select title, level, coalesce(topic, '(none)') as topic, page_id
                           from read_parquet('{ds}/articles.parquet') where page_id is not null order by title""").fetchnumpy()
    na = len(arts["title"])
    con.register("a_tbl", pa.table({"rn": np.arange(na, dtype=np.int64), "page_id": arts["page_id"].astype(np.int64)}))
    ac = con.execute(f"""select a.rn, nd.idx from a_tbl a
        join read_parquet('{ds}/article_categories.parquet') ac on ac.page_id = a.page_id and not ac.hidden
        join read_parquet('{cg}/nodes.parquet') nd on nd.title = ac.category and not nd.hidden""").fetchnumpy()
    rn, ci = ac["rn"].astype(np.int64), ac["idx"].astype(np.int64)
    order = np.argsort(rn, kind="stable")
    rn, ci = rn[order], ci[order]
    starts = np.r_[0, np.flatnonzero(np.diff(rn)) + 1]
    has = rn[starts]                                   # article rows that have >= 1 visible category
    ncat = np.diff(np.r_[starts, len(rn)])
    share = np.zeros((na, T))
    mind = np.full((na, T), 127.0)
    for j in range(T):
        d = D[ci, j].astype(np.int16)
        reach = d >= 0
        share[has, j] = np.add.reduceat(reach.astype(np.int64), starts) / ncat
        mind[has, j] = np.minimum.reduceat(np.where(reach, d, 127), starts)
    log("article scores computed")

    nontop = np.array([nm in NON_TOPICAL for nm in top_names])
    sh2 = share.copy()
    sh2[:, nontop] = 0
    methods = {
        "share": np.where(share.max(axis=1) > 0, share.argmax(axis=1), -1),
        "nearest": np.where(mind.min(axis=1) < 127, (mind - 0.5 * share).argmin(axis=1), -1),
        "topical": np.where(sh2.max(axis=1) > 0, sh2.argmax(axis=1), -1),
    }
    topics = [str(t) for t in arts["topic"]]
    levels = arts["level"]
    topic_names = sorted(set(topics))
    ti = np.array([topic_names.index(t) for t in topics])
    names_ext = top_names + ["(none)"]

    def table(best, sel=None):
        M = np.zeros((len(topic_names), T + 1), dtype=np.int64)
        idxs = np.arange(na) if sel is None else np.where(sel)[0]
        np.add.at(M, (ti[idxs], np.where(best[idxs] >= 0, best[idxs], T)), 1)
        return M

    def score_of(M):
        tot = M.sum()
        return {"articles": int(tot), "nmi": round(nmi(M[:, :T]), 4) if M[:, :T].sum() else None,
                "vital_given_category": round(float(M.max(axis=0).sum() / tot), 4),
                "category_given_vital": round(float(M.max(axis=1).sum() / tot), 4)}

    res = {"articles": na, "top_level_topics": top_names, "vital_topics": topic_names, "methods": {}}
    for name, best in methods.items():
        M = table(best)
        r = {"all": score_of(M), "level_le_3": score_of(table(best, levels <= 3)),
             "level_le_4": score_of(table(best, levels <= 4)),
             "share_per_top": {names_ext[j]: int(M[:, j].sum()) for j in np.argsort(-M.sum(axis=0))[:8]}}
        r["by_vital_topic"] = {}
        for t in topic_names:
            row = M[topic_names.index(t)]
            o = np.argsort(-row)[:4]
            r["by_vital_topic"][t] = {"n": int(row.sum()), "top": [(names_ext[j], round(float(row[j] / row.sum()), 3)) for j in o if row[j] > 0]}
        r["by_category_topic"] = {}
        for j, nm in enumerate(names_ext):
            col = M[:, j]
            if col.sum() == 0:
                continue
            o = np.argsort(-col)[:3]
            r["by_category_topic"][nm] = {"n": int(col.sum()), "top": [(topic_names[i], round(float(col[i] / col.sum()), 3)) for i in o if col[i] > 0]}
        res["methods"][name] = r
        np.savetxt(out / f"crosstab_{name}.csv", M, fmt="%d", delimiter=",", header=",".join(names_ext), comments="")
        log(f"{name}: NMI {r['all']['nmi']}, purity V|C {r['all']['vital_given_category']}, C|V {r['all']['category_given_vital']}")
    (out / "crosstab_rows.txt").write_text("\n".join(topic_names), encoding="utf-8")

    # ---- Wikidata side: most common direct types per Vital topic (with English labels)
    lab = f"read_parquet('{qp}/class_labels.parquet')"
    rows = con.execute(f"""
        select topic, type, coalesce(l.label, type) as label, n from (
          select a.topic, t.type, count(*) as n, row_number() over (partition by a.topic order by count(*) desc) as rk
          from read_parquet('{ds}/articles.parquet') a join read_parquet('{ds}/article_types.parquet') t on t.qid = a.qid
          where a.topic is not null group by 1, 2) x
        left join (select c, first(label) as label from {lab} group by 1) l on l.c = x.type
        where rk <= 5 order by topic, rk""").fetchall()
    res["top_p31_types_by_topic"] = {}
    for t, ty, lb, cnt in rows:
        res["top_p31_types_by_topic"].setdefault(t, []).append((ty, lb, cnt))
    (out / "topic_crosstab.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    log("done")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
