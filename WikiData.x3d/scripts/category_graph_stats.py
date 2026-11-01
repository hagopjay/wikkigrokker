#!/usr/bin/env python3
"""Whole-English-Wikipedia category graph: size, cycles, branching, depth and article coverage.

usage: python3 -I category_graph_stats.py ENWIKI_PARQUET_DIR OUT_DIR

Reads   page, page_props, category, linktarget, categorylinks (Parquet from sqldump_to_parquet.py)
Writes  OUT_DIR/nodes.parquet   one row per category (idx, cat_id, title, pages, subcats, files, hidden,
                                scc_size, dist_main)  - dist_main = shortest downward distance from
                                Category:Main topic classifications over visible sub-category edges
        OUT_DIR/edges.parquet   (child, parent) as dense node indices (all subcat links)
        OUT_DIR/article_depth.parquet  (page_id, n_cats, n_visible, n_reach, min_dist) for ns-0 non-redirect pages
        OUT_DIR/stats.json      the numbers
"""
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

SPECIAL = ["Main_topic_classifications", "Contents", "Fundamental_categories", "Articles", "Hidden_categories"]


def main(wp, out):
    wp, out = Path(wp), Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "tmp").mkdir()
    t0 = time.time()
    stats = {"seconds": {}}

    def log(msg, key=None):
        el = time.time() - t0
        if key:
            stats["seconds"][key] = round(el, 1)
        print(f"[{el:7.1f}s] {msg}", flush=True)

    con = duckdb.connect()
    con.execute(f"pragma threads=2; pragma memory_limit='4GB'; pragma temp_directory='{out / 'tmp'}'")
    P = lambda n: f"read_parquet('{wp}/{n}.parquet')"
    q1 = lambda s: con.execute(s).fetchone()[0]

    # ---- nodes (one per row of the category table) with hidden flag and dense index
    con.execute(f"""create table hid as select p.page_title as cat_title from {P('page')} p
        where p.page_namespace = 14 and p.page_id in (select pp_page from {P('page_props')} where pp_propname = 'hiddencat')""")
    con.execute(f"""create table nodes as
        select cast(row_number() over (order by c.cat_id) - 1 as integer) as idx, c.cat_id, c.cat_title,
               c.cat_pages, c.cat_subcats, c.cat_files, (h.cat_title is not null) as hidden
        from {P('category')} c left join hid h on h.cat_title = c.cat_title""")
    n_nodes = q1("select count(*) from nodes")
    stats["nodes"] = {"all": n_nodes, "hidden": q1("select count(*) from nodes where hidden"),
                      "visible": q1("select count(*) from nodes where not hidden"),
                      "empty_visible": q1("select count(*) from nodes where not hidden and cat_pages = 0")}
    log(f"{n_nodes} categories ({stats['nodes']['hidden']} hidden)", "nodes")

    # ---- edges: every sub-category link  child -> parent
    con.execute(f"""create table cpage as select page_id, page_title from {P('page')} where page_namespace = 14""")
    con.execute(f"""create table sub as
        select cl.cl_from as child_page, lt.lt_title as parent_title
        from {P('categorylinks')} cl join {P('linktarget')} lt on lt.lt_id = cl.cl_target_id and lt.lt_namespace = 14
        where cl.cl_type = 'subcat'""")
    stats["subcat_links_in_dump"] = q1("select count(*) from sub")
    con.execute("""create table edges as
        select distinct cn.idx as child, pn.idx as parent
        from sub s join cpage p on p.page_id = s.child_page
        join nodes cn on cn.cat_title = p.page_title
        join nodes pn on pn.cat_title = s.parent_title""")
    en = con.execute("select child, parent from edges").fetchnumpy()
    child, parent = en["child"].astype(np.int32), en["parent"].astype(np.int32)
    E = len(child)
    selfloop = int((child == parent).sum())
    stats["edges"] = {"subcat_links": stats["subcat_links_in_dump"], "distinct_edges": E, "self_loops": selfloop}
    pq.write_table(pa.table({"child": child, "parent": parent}), out / "edges.parquet", compression="zstd")
    log(f"{E} distinct sub-category edges ({selfloop} self-loops)", "edges")

    hidden = np.zeros(n_nodes, dtype=bool)
    hidden[np.array([r[0] for r in con.execute("select idx from nodes where hidden").fetchall()], dtype=np.int64)] = True
    pages = con.execute("select idx, cat_pages, cat_subcats, cat_files from nodes order by idx").fetchnumpy()

    def graph_stats(name, mask_nodes):
        """mask_nodes: boolean array of nodes kept.  Edges parent -> child kept when both ends are kept."""
        keep = mask_nodes[child] & mask_nodes[parent] & (child != parent)
        c, p = child[keep], parent[keep]
        A = csr_matrix((np.ones(len(c), dtype=np.int8), (p, c)), shape=(n_nodes, n_nodes))
        ncomp, labels = connected_components(A, directed=True, connection="strong")
        labels = np.where(mask_nodes, labels, -1)
        sizes = np.bincount(labels[labels >= 0], minlength=ncomp)
        big = np.where(sizes > 1)[0]
        n_par = np.bincount(c, minlength=n_nodes)       # parents per node
        n_kid = np.bincount(p, minlength=n_nodes)       # children per node
        nn = int(mask_nodes.sum())
        has_par = n_par[mask_nodes]
        res = {
            "nodes": nn, "edges": int(len(c)),
            "roots_without_parent": int((has_par == 0).sum()),
            "leaves_without_child": int((n_kid[mask_nodes] == 0).sum()),
            "with_2plus_parents": int((has_par >= 2).sum()),
            "parents_per_node": {"mean": round(float(has_par[has_par > 0].mean()), 2),
                                 "median": float(np.median(has_par[has_par > 0])),
                                 "p90": float(np.percentile(has_par[has_par > 0], 90)), "max": int(has_par.max())},
            "children_per_node_max": int(n_kid.max()),
            "scc_count_nontrivial": int(len(big)), "nodes_in_cycles": int(sizes[big].sum()) if len(big) else 0,
            "largest_scc": int(sizes.max()) if len(sizes) else 0,
            "top_scc_sizes": sorted(sizes[big].tolist(), reverse=True)[:10],
        }
        return A, labels, sizes, res

    # ---- all categories vs visible categories
    A_all, lab_all, sz_all, st_all = graph_stats("all", np.ones(n_nodes, dtype=bool))
    stats["graph_all"] = st_all
    log(f"all-categories graph: {st_all['scc_count_nontrivial']} cyclic SCCs, largest {st_all['largest_scc']}", "scc_all")
    A, lab, sz, st_vis = graph_stats("visible", ~hidden)
    stats["graph_visible"] = st_vis
    log(f"visible graph: {st_vis['nodes']} nodes, {st_vis['edges']} edges, {st_vis['scc_count_nontrivial']} cyclic SCCs "
        f"({st_vis['nodes_in_cycles']} nodes), largest {st_vis['largest_scc']}", "scc_visible")

    # ---- special nodes and depth below Main topic classifications
    idx_of = {}
    for t in SPECIAL:
        r = con.execute("select idx from nodes where cat_title = encode(?)", [t]).fetchall()
        idx_of[t] = r[0][0] if r else None
    stats["special_nodes"] = idx_of
    dist = np.full(n_nodes, np.inf)
    src = idx_of["Main_topic_classifications"]
    if src is not None:
        dist = dijkstra(A, directed=True, indices=src, unweighted=True)
    reached = np.isfinite(dist) & ~hidden
    dd = dist[reached].astype(int)
    stats["main_topic_tree"] = {
        "reached_visible_categories": int(reached.sum()),
        "share_of_visible": round(float(reached.sum()) / max(1, st_vis["nodes"]), 4),
        "depth_histogram": {int(k): int(v) for k, v in zip(*np.unique(dd, return_counts=True))},
        "max_depth": int(dd.max()) if len(dd) else None,
        "direct_children": int(A[src].nnz) if src is not None else None,
    }
    log(f"Main topic classifications reaches {int(reached.sum())} visible categories (max depth {stats['main_topic_tree']['max_depth']})", "bfs_main")
    dist_c = np.full(n_nodes, np.inf)
    if idx_of["Contents"] is not None:
        dist_c = dijkstra(A, directed=True, indices=idx_of["Contents"], unweighted=True)
    stats["contents_tree"] = {"reached_visible_categories": int((np.isfinite(dist_c) & ~hidden).sum())}

    # ---- node table with decoded titles
    titles = [b.decode("utf-8", "replace").replace("_", " ") for b in
              con.execute("select cat_title from nodes order by idx").fetchnumpy()["cat_title"]]
    cat_ids = con.execute("select cat_id from nodes order by idx").fetchnumpy()["cat_id"]
    nodes_tbl = pa.table({
        "idx": np.arange(n_nodes, dtype=np.int32), "cat_id": cat_ids, "title": titles,
        "pages": pages["cat_pages"], "subcats": pages["cat_subcats"], "files": pages["cat_files"],
        "hidden": hidden, "scc_size": np.where(lab >= 0, sz[np.maximum(lab, 0)], 0).astype(np.int32),
        "dist_main": np.where(np.isfinite(dist) & ~hidden, dist, -1).astype(np.int16),
    })
    pq.write_table(nodes_tbl, out / "nodes.parquet", compression="zstd")

    # examples: biggest cyclic SCCs and the main-topic children
    ex = []
    for comp in np.argsort(-sz)[:4]:
        if sz[comp] <= 1:
            continue
        members = np.where(lab == comp)[0]
        top = members[np.argsort(-pages["cat_pages"][members])[:10]]
        ex.append({"size": int(sz[comp]), "examples": [titles[i] for i in top]})
    stats["scc_examples"] = ex
    if src is not None:
        kids = A[src].indices
        stats["main_topic_children"] = sorted(
            [(titles[i], int(pages["cat_pages"][i]), int(pages["cat_subcats"][i])) for i in kids], key=lambda x: -x[1])
    roots = np.where((np.bincount(child[~hidden[child] & ~hidden[parent]], minlength=n_nodes) == 0) & ~hidden)[0]
    top_roots = roots[np.argsort(-(pages["cat_pages"][roots] + pages["cat_subcats"][roots]))[:15]]
    stats["largest_roots"] = [(titles[i], int(pages["cat_pages"][i]), int(pages["cat_subcats"][i])) for i in top_roots]
    log("node table written", "nodes_parquet")

    # ---- article coverage by the Main topic tree
    reach_tbl = pa.table({"idx": np.where(reached)[0].astype(np.int32), "dist": dist[reached].astype(np.int16)})
    con.register("reach_arrow", reach_tbl)
    con.execute("create table reach as select * from reach_arrow")
    con.execute(f"""create table lt14 as
        select lt.lt_id, n.idx, n.hidden, r.dist
        from {P('linktarget')} lt join nodes n on n.cat_title = lt.lt_title
        left join reach r on r.idx = n.idx where lt.lt_namespace = 14""")
    log(f"{q1('select count(*) from lt14')} category link targets mapped", "lt14")
    con.execute(f"""create table art as
        select page_id from {P('page')} where page_namespace = 0 and page_is_redirect = 0""")
    n_art = q1("select count(*) from art")
    con.execute(f"""create table art_stats as
        select cl.cl_from as page_id, count(*) as n_cats,
               sum(case when t.hidden then 0 else 1 end) as n_visible,
               count(t.dist) as n_reach, min(t.dist) as min_dist
        from {P('categorylinks')} cl join lt14 t on t.lt_id = cl.cl_target_id
        where cl.cl_type = 'page' group by 1""")
    con.execute("""create table art_depth as
        select a.page_id, coalesce(s.n_cats, 0) as n_cats, coalesce(s.n_visible, 0) as n_visible,
               coalesce(s.n_reach, 0) as n_reach, s.min_dist
        from art a left join art_stats s on s.page_id = a.page_id""")
    con.execute(f"copy art_depth to '{out}/article_depth.parquet' (format parquet, compression zstd)")
    cov = {
        "articles": n_art,
        "no_category_at_all": q1("select count(*) from art_depth where n_cats = 0"),
        "only_hidden_categories": q1("select count(*) from art_depth where n_cats > 0 and n_visible = 0"),
        "with_visible_category": q1("select count(*) from art_depth where n_visible > 0"),
        "under_main_topic_tree": q1("select count(*) from art_depth where n_reach > 0"),
        "visible_but_outside_main_topic_tree": q1("select count(*) from art_depth where n_visible > 0 and n_reach = 0"),
        "visible_categories_per_article": dict(zip(["mean", "median", "p90", "max"], con.execute(
            "select round(avg(n_visible), 2), median(n_visible), quantile_cont(n_visible, 0.9), max(n_visible) from art_depth").fetchone())),
        "min_depth_cumulative": {int(d): q1(f"select count(*) from art_depth where min_dist <= {d}") for d in (2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30)},
        "min_depth_histogram": {int(r[0]): int(r[1]) for r in con.execute(
            "select min_dist, count(*) from art_depth where min_dist is not null group by 1 order by 1").fetchall()},
    }
    stats["article_coverage"] = cov
    log(f"{cov['under_main_topic_tree']} of {n_art} articles sit under the Main topic tree", "coverage")

    (out / "stats.json").write_text(json.dumps(stats, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k not in ("scc_examples", "main_topic_children", "largest_roots")},
                     indent=1, ensure_ascii=False, default=str))
    log("done")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
