#!/usr/bin/env python3
"""Step 2: the type spine - a primary tree over Wikidata classes with a YAGO/schema.org scaffold on top,
and a primary type for every English Wikipedia article.

usage: python3 -I build_type_spine.py QLEVER2_DIR QLEVER_PARQUET_DIR ENWIKI_PARQUET_DIR YAGO_DIR ADDENDA.csv OUT_DIR

QLEVER2_DIR         class_edges_ext.tsv, class_labels_ext.tsv, item_p279.tsv   (queries/*.rq)
QLEVER_PARQUET_DIR  item_types.parquet                                          (P31 pairs)
ENWIKI_PARQUET_DIR  page.parquet, page_props.parquet
YAGO_DIR            schema_block.ttl (head of the YAGO 4.5 file), subclass_lines.txt (its rdfs:subClassOf lines)
ADDENDA.csv         hand-checked additions to the scaffold (config/scaffold_addenda.csv)

Follows the plan, in order:
 1. subclass-of edges -> strongly connected components, each collapsed to one node, all listed in the report
 2. direct and reach counts per class (reach = distinct articles under the class via any path)
 3. scaffold: YAGO's schema.org classes anchored to Wikidata classes (ys:fromClass) plus the addenda
 4. primary parent per class by the ordered rule: scaffold edge, closer to scaffold root, larger count,
    two independent sources, lowest id; the other parents stay as alternates
 5. each article gets its direct types (P31, else P279) and a primary type; classes with fewer than
    M_FOLD primary articles fold into their primary parent
Outputs (OUT_DIR): classes.parquet, edges.parquet, article_type.parquet, scc_components.json,
                   orphans_top.csv, report.json
"""
import csv
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import duckdb
import networkx as nx
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

M_FOLD = 50
INF = 10 ** 6
QID = re.compile(r"Q(\d+)")
RULES = ["closer to scaffold root", "larger count", "two sources", "lowest id"]


def qnum(s):
    m = QID.search(s)
    return int(m.group(1)) if m else None


def read_pairs(path):
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                yield parts[0], parts[1]


def norm(s):
    return re.sub(r"\s+", " ", s.replace("_", " ")).strip().lower()


def parse_yago_schema(path):
    """name -> {'parent': name|None, 'qids': [int]} from the shape blocks at the head of the YAGO file."""
    cls = {}
    subj = None
    for line in open(path, encoding="utf-8"):
        if line.strip() and not line[0].isspace() and not line.startswith(("@prefix", "#")):
            m = re.match(r"(\S+)\s", line)
            if m:
                subj = m.group(1)
        if subj and subj.startswith(("schema:", "yago:")):
            c = cls.setdefault(subj, {"parent": None, "qids": []})
            if "ys:fromClass" in line:
                c["qids"] += [int(x) for x in re.findall(r"wd:Q(\d+)", line)]
            m = re.search(r"rdfs:subClassOf\s+((?:schema|yago):\w+)", line)
            if m:
                c["parent"] = m.group(1)
    return cls


def parse_yago_taxonomy(path):
    rx = re.compile(r"^(\S+)\trdfs:subClassOf\t(\S+)\t\.$")
    out = []
    for line in open(path, encoding="utf-8", errors="replace"):
        m = rx.match(line.rstrip("\n"))
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def main(q2, qp, wp, yago, addenda, out):
    q2, qp, wp, yago, out = map(Path, (q2, qp, wp, yago, out))
    out.mkdir(parents=True, exist_ok=False)
    (out / "tmp").mkdir()
    t0 = time.time()
    rep = {"seconds": {}}

    def log(msg, key=None):
        el = time.time() - t0
        if key:
            rep["seconds"][key] = round(el, 1)
        print(f"[{el:7.1f}s] {msg}", flush=True)

    con = duckdb.connect()
    con.execute(f"pragma threads=2; pragma memory_limit='5GB'; pragma temp_directory='{out / 'tmp'}'")
    P = lambda n: f"read_parquet('{wp}/{n}.parquet')"
    one = lambda s: con.execute(s).fetchall()[0][0]       # fetchall: leave no open result behind

    # ---------------------------------------------------------------- articles and direct types
    con.execute(f"""create table art as
        select p.page_id, cast(substr(decode(pp.pp_value), 2) as bigint) as qid
        from {P('page')} p join {P('page_props')} pp on pp.pp_page = p.page_id and pp.pp_propname = 'wikibase_item'
        where p.page_namespace = 0 and p.page_is_redirect = 0""")
    rep["articles_total"] = one(f"select count(*) from {P('page')} where page_namespace = 0 and page_is_redirect = 0")
    rep["articles_with_item"] = one("select count(*) from art")
    con.execute(f"""create table it as
        select cast(substr(item, 2) as bigint) as item, cast(substr(type, 2) as bigint) as t
        from read_parquet('{qp}/item_types.parquet')""")
    con.execute("create table p31 as select distinct a.qid as art, i.t from art a join it i on i.item = a.qid")
    pairs = ((qnum(x), qnum(y)) for x, y in read_pairs(q2 / "item_p279.tsv"))
    p279 = sorted({(a, b) for a, b in pairs if a is not None and b is not None})
    con.register("p279_all", pa.table({"item": pa.array([a for a, _ in p279], pa.int64()),
                                       "t": pa.array([b for _, b in p279], pa.int64())}))
    con.execute("""create table p279a as select distinct a.qid as art, p.t from art a
                   join p279_all p on p.item = a.qid where a.qid not in (select art from p31)""")
    con.execute("""create table direct as select art, t, 'P31' as src from p31
                   union all select art, t, 'P279' as src from p279a""")
    log(f"{rep['articles_with_item']} articles with an item; {one('select count(distinct art) from p31')} with P31, "
        f"{one('select count(distinct art) from p279a')} typed by P279 only", "articles")

    # ---------------------------------------------------------------- 1. class graph and cycles
    raw = {(qnum(a), qnum(b)) for a, b in read_pairs(q2 / "class_edges_ext.tsv")}
    raw = np.array(sorted((c, p) for c, p in raw if c is not None and p is not None and c != p), dtype=np.int64)
    dtypes = np.array([r[0] for r in con.execute("select distinct t from direct").fetchall()], dtype=np.int64)
    nodes = np.unique(np.concatenate([raw.ravel(), dtypes]))
    N = len(nodes)
    ci, pi = np.searchsorted(nodes, raw[:, 0]), np.searchsorted(nodes, raw[:, 1])
    A = csr_matrix((np.ones(len(ci), dtype=np.int8), (ci, pi)), shape=(N, N))
    ncomp, comp = connected_components(A, directed=True, connection="strong")
    draw = dict(con.execute("select t, count(distinct art) from direct group by 1").fetchall())
    D_raw = np.array([draw.get(int(q), 0) for q in nodes])
    order = np.lexsort((nodes, -D_raw))
    ucomp, first = np.unique(comp[order], return_index=True)
    rep_of_comp = np.zeros(ncomp, dtype=np.int64)
    rep_of_comp[ucomp] = nodes[order[first]]
    rep_q = rep_of_comp[comp]                              # representative QID of every node
    sizes = np.bincount(comp, minlength=ncomp)
    labels = {}
    for a, b in read_pairs(q2 / "class_labels_ext.tsv"):
        q = qnum(a)
        if q is not None and q not in labels:
            labels[q] = b.rsplit("@", 1)[0].strip('"')
    lab = lambda q: labels.get(int(q), f"(no English label) Q{int(q)}")
    comps = []
    for c in np.where(sizes > 1)[0]:
        mem = nodes[comp == c]
        comps.append({"size": int(len(mem)), "representative": f"Q{int(rep_of_comp[c])} {lab(rep_of_comp[c])}",
                      "members": [f"Q{int(q)} {lab(q)}" for q in mem[:12]]})
    comps.sort(key=lambda x: -x["size"])
    (out / "scc_components.json").write_text(json.dumps(comps, indent=1, ensure_ascii=False), encoding="utf-8")
    rep["cycles"] = {"components": len(comps), "classes_merged": int(sum(c["size"] for c in comps)),
                     "largest": comps[0]["size"] if comps else 0}
    cr, pr = rep_q[ci], rep_q[pi]
    keep = cr != pr
    E = np.unique(np.stack([cr[keep], pr[keep]], axis=1), axis=0)
    reps = np.unique(rep_q)
    rep_map = {int(q): int(r) for q, r in zip(nodes, rep_q) if q != r}
    log(f"{N} classes, {len(E)} condensed edges, {len(comps)} cyclic components", "graph")

    con.register("rep_map_t", pa.table({"q": pa.array(list(rep_map), pa.int64()), "r": pa.array(list(rep_map.values()), pa.int64())}))
    con.execute("""create table direct_rep as select distinct d.art, coalesce(m.r, d.t) as t, d.src
                   from direct d left join rep_map_t m on m.q = d.t""")
    D = dict(con.execute("select t, count(distinct art) from direct_rep group by 1").fetchall())

    # ---------------------------------------------------------------- 3. scaffold
    sch = parse_yago_schema(yago / "schema_block.ttl")
    sch.setdefault("schema:Thing", {"parent": None, "qids": []})
    with open(addenda, encoding="utf-8") as f:
        add_rows = list(csv.DictReader(f))
    for r in add_rows:
        if r["kind"] == "class":
            sch.setdefault(r["name"], {"parent": r["target"], "qids": []})["parent"] = r["target"]
    sch["wm:Unclassified"] = {"parent": "schema:Thing", "qids": []}
    label_warnings = []
    for r in add_rows:                                   # 'unmap': drop a YAGO anchor that is too broad for our tree
        if r["kind"] == "unmap":
            q = int(r["name"][1:])
            if r["expected_label"] and norm(lab(q)) != norm(r["expected_label"]):
                label_warnings.append(f"{r['name']}: wanted '{r['expected_label']}', Wikidata says '{lab(q)}'")
            sch[r["target"]]["qids"] = [x for x in sch[r["target"]]["qids"] if x != q]
    for r in add_rows:
        if r["kind"] == "map":
            q = int(r["name"][1:])
            if r["expected_label"] and norm(lab(q)) != norm(r["expected_label"]):
                label_warnings.append(f"{r['name']}: wanted '{r['expected_label']}', Wikidata says '{lab(q)}'")
            sch.setdefault(r["target"], {"parent": "schema:Thing", "qids": []})["qids"].append(q)
    rep["addenda_label_warnings"] = label_warnings
    names = sorted(sch)
    sid = {n: -(i + 1) for i, n in enumerate(names)}
    sdepth = {}

    def depth_of(n):
        if n not in sdepth:
            sdepth[n] = 0 if n == "schema:Thing" else 1 + depth_of(sch[n]["parent"] or "schema:Thing")
        return sdepth[n]

    for n in names:
        depth_of(n)
    node_set = set(int(q) for q in reps)
    anchor_of, missing_anchor = defaultdict(list), 0
    for n in names:
        for q in sch[n]["qids"]:
            r = rep_map.get(q, q)
            if r in node_set:
                if n not in anchor_of[r]:
                    anchor_of[r].append(n)
            else:
                missing_anchor += 1
    rep["scaffold"] = {"classes": len(names), "anchors": len(anchor_of), "mapped_qids_not_in_graph": missing_anchor}
    UNCL, THING = sid["wm:Unclassified"], sid["schema:Thing"]

    # second source for the primary-parent rule: YAGO taxonomy edges projected onto Wikidata ids by label
    lab2q = defaultdict(set)
    for q in reps:
        lab2q[norm(lab(q))].add(int(q))
    uniq = {l: next(iter(s)) for l, s in lab2q.items() if len(s) == 1 and not l.startswith("(no english")}
    anchors_of_name = {n: [rep_map.get(q, q) for q in sch[n]["qids"] if rep_map.get(q, q) in node_set] for n in names}
    two = set()
    for c, p in parse_yago_taxonomy(yago / "subclass_lines.txt"):
        qc = uniq.get(norm(c[5:])) if c.startswith("yago:") else None
        if qc is None:
            continue
        if p.startswith("yago:") and p[5:] and uniq.get(norm(p[5:])):
            two.add((qc, uniq[norm(p[5:])]))
        for qa in anchors_of_name.get(p, []):
            two.add((qc, qa))
    wd_edges = {(int(c), int(p)) for c, p in E}
    two &= wd_edges
    rep["two_source_edges"] = len(two)

    # distance from the scaffold root over subclass edges (parents before children)
    parents = defaultdict(list)
    for c, p in E:
        parents[int(c)].append(int(p))
    G = nx.DiGraph()
    G.add_nodes_from(int(q) for q in reps)
    G.add_edges_from((int(p), int(c)) for c, p in E)
    dist = {int(q): INF for q in reps}
    for r, Ss in anchor_of.items():
        dist[r] = min(sdepth[s] for s in Ss) + 1
    for v in nx.topological_sort(G):
        for p in parents.get(v, ()):
            if dist[p] + 1 < dist[v]:
                dist[v] = dist[p] + 1
    n_scaf = sum(1 for v in dist.values() if v < INF)
    log(f"{len(anchor_of)} anchors; {n_scaf} classes sit below the scaffold, {len(dist) - n_scaf} do not", "scaffold")

    # ---------------------------------------------------------------- 2. reach counts
    edges_all = [(int(c), int(p)) for c, p in E]
    for r, Ss in anchor_of.items():
        for s in Ss:
            edges_all.append((r, sid[s]))
    for n in names:
        if n != "schema:Thing":
            edges_all.append((sid[n], sid[sch[n]["parent"] or "schema:Thing"]))
    for v in dist:
        if dist[v] >= INF and not parents.get(v):
            edges_all.append((v, UNCL))
    con.register("edges_all", pa.table({"c": pa.array([a for a, _ in edges_all], pa.int64()),
                                        "p": pa.array([b for _, b in edges_all], pa.int64())}))
    con.execute("""create table sets as
        select row_number() over () as sid, ts, n from (
          select ts, count(*) as n from (select art, list(t order by t) as ts
                                         from (select distinct art, t from direct_rep) group by art) group by ts)""")
    con.execute("create table sets_x as select sid, unnest(ts) as t, n from sets")
    con.execute("""create table closure as
        with recursive r(c, a) as (select c, p from edges_all union select r.c, e.p from r join edges_all e on e.c = r.a)
        select c, a from r""")
    con.execute("""create table closure_self as select c, a from closure
                   union select t as c, t as a from (select distinct t from sets_x)""")
    R = dict(con.execute("""select a, sum(n) from (select distinct sx.sid, cs.a, sx.n from sets_x sx
                            join closure_self cs on cs.c = sx.t) group by a""").fetchall())
    log(f"{one('select count(*) from sets')} distinct type sets; closure {one('select count(*) from closure')} pairs", "reach")

    # ---------------------------------------------------------------- 4. primary parents
    par, rule = {}, {}
    alt = []
    for v in sorted(dist):
        ps = parents.get(v, [])
        if v in anchor_of:
            s = sorted(anchor_of[v], key=lambda n: (-sdepth[n], n))[0]
            par[v], rule[v] = sid[s], "scaffold edge"
            alt += [(v, p, "anchor keeps its Wikidata parent as alternate") for p in ps]
            continue
        if not ps:
            par[v], rule[v] = UNCL, "root"
            continue
        if dist[v] < INF:
            cands = [p for p in ps if dist[p] < INF]
            key = lambda p: (dist[p], -R.get(p, 0), 0 if (v, p) in two else 1, p)
        else:
            cands = list(ps)
            key = lambda p: (0, -R.get(p, 0), 0 if (v, p) in two else 1, p)
        cands.sort(key=key)
        par[v] = cands[0]
        if len(cands) == 1:
            rule[v] = "only parent"
        else:
            a, b = key(cands[0]), key(cands[1])
            rule[v] = RULES[next(i for i in range(4) if a[i] != b[i])]
        alt += [(v, p, "not chosen") for p in ps if p != cands[0]]
    for n in names:
        if n != "schema:Thing":
            par[sid[n]], rule[sid[n]] = sid[sch[n]["parent"] or "schema:Thing"], "scaffold"

    # depth in the primary tree
    depth = {THING: 0}
    for v in list(par):
        stack = []
        u = v
        while u not in depth:
            stack.append(u)
            u = par[u]
        for w in reversed(stack):
            depth[w] = depth[par[w]] + 1
    children = defaultdict(list)
    for v, p in par.items():
        children[p].append(v)
    log(f"primary tree: {len(par)} nodes, max depth {max(depth.values())}", "primary")

    # ---------------------------------------------------------------- 5. article primary types, counts, folding
    allids = sorted(par) + [THING]
    insc = {v: (dist.get(v, 0) < INF) for v in allids}
    ci_tbl = pa.table({"id": pa.array(allids, pa.int64()), "depth": pa.array([depth[v] for v in allids], pa.int32()),
                       "d": pa.array([D.get(v, 0) for v in allids], pa.int64()),
                       "insc": pa.array([insc[v] for v in allids], pa.bool_())})
    con.register("classinfo", ci_tbl)
    con.execute("""create table pt as
        select art, t as ptype from (
          select dr.art, dr.t, row_number() over (partition by dr.art order by ci.insc desc, ci.depth desc, ci.d desc, dr.t asc) as rn
          from direct_rep dr join classinfo ci on ci.id = dr.t) where rn = 1""")
    own = dict(con.execute("select ptype, count(*) from pt group by 1").fetchall())
    PC = {v: own.get(v, 0) for v in allids}
    for v in sorted(allids, key=lambda x: -depth[x]):
        if v != THING:
            PC[par[v]] += PC[v]
    folded = {v: (PC[v] < M_FOLD and v > 0 and v in par) for v in allids}      # scaffold nodes (negative ids) never fold
    disp = {}
    for v in sorted(allids, key=lambda x: depth[x]):
        disp[v] = v if (v == THING or not folded[v]) else disp[par[v]]
    con.register("dispmap", pa.table({"id": pa.array(list(disp), pa.int64()), "disp": pa.array(list(disp.values()), pa.int64())}))
    con.execute("""create table article_type as
        select a.page_id, a.qid, pt.ptype as primary_type, dm.disp as display_class,
               case when a.qid in (select art from p31) then 'P31' when a.qid in (select art from p279a) then 'P279' else 'none' end as source
        from art a left join pt on pt.art = a.qid left join dispmap dm on dm.id = pt.ptype""")
    con.execute(f"copy article_type to '{out}/article_type.parquet' (format parquet, compression zstd)")
    log("article types written", "articles_out")

    # ---------------------------------------------------------------- tables and report
    def name_of(v):
        return names[-v - 1] if v < 0 else f"Q{v}"

    def label_of(v):
        return name_of(v) if v < 0 else lab(v)

    scc_size = {int(rep_of_comp[c]): int(sizes[c]) for c in range(ncomp)}
    rows = {"id": [], "name": [], "label": [], "kind": [], "scc_size": [], "dist_to_scaffold": [], "parent": [],
            "rule": [], "depth": [], "direct": [], "reach": [], "primary_count": [], "folded": [], "n_children": []}
    for v in allids:
        rows["id"].append(v)
        rows["name"].append(name_of(v))
        rows["label"].append(label_of(v))
        rows["kind"].append("scaffold" if v < 0 else ("anchor" if v in anchor_of else "class"))
        rows["scc_size"].append(scc_size.get(v, 1))
        rows["dist_to_scaffold"].append(dist[v] if (v > 0 and dist[v] < INF) else (-1 if v > 0 else sdepth[name_of(v)]))
        rows["parent"].append(par.get(v, 0))
        rows["rule"].append(rule.get(v, ""))
        rows["depth"].append(depth[v])
        rows["direct"].append(D.get(v, 0))
        rows["reach"].append(int(R.get(v, 0)))
        rows["primary_count"].append(PC[v])
        rows["folded"].append(bool(folded[v]))
        rows["n_children"].append(len(children.get(v, ())))
    pq.write_table(pa.table(rows), out / "classes.parquet", compression="zstd")
    erows = {"child": [], "parent": [], "role": [], "reason": [], "two_source": []}
    for v, p in par.items():
        erows["child"].append(v)
        erows["parent"].append(p)
        erows["role"].append("primary" if v > 0 else "scaffold")
        erows["reason"].append(rule[v])
        erows["two_source"].append(bool((v, p) in two))
    for v, p, why in alt:
        erows["child"].append(v)
        erows["parent"].append(p)
        erows["role"].append("alternate")
        erows["reason"].append(why)
        erows["two_source"].append(bool((v, p) in two))
    pq.write_table(pa.table(erows), out / "edges.parquet", compression="zstd")

    orph = sorted((v for v in dist if dist[v] >= INF and D.get(v, 0) > 0), key=lambda v: -D[v])[:80]
    with open(out / "orphans_top.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qid", "label", "articles_direct", "reach", "primary_parent"])
        for v in orph:
            w.writerow([f"Q{v}", lab(v), D[v], R.get(v, 0), f"{label_of(par[v])} ({name_of(par[v])})"])

    typed = one("select count(*) from article_type where primary_type is not null")
    under_uncl = 0
    for v in own:
        u = v
        while u != THING and u != UNCL:
            u = par[u]
        if u == UNCL:
            under_uncl += own[v]
    by_rule = defaultdict(int)
    for v, r in rule.items():
        if v > 0:
            by_rule[r] += 1
    add_ok = all(PC[v] == own.get(v, 0) + sum(PC[c] for c in children.get(v, ())) for v in allids)
    rep.update({
        "classes": len(dist), "scaffold_nodes": len(names), "tree_nodes": len(allids), "max_depth": max(depth.values()),
        "articles_typed": typed, "typed_via_P31": one("select count(*) from article_type where source = 'P31'"),
        "typed_via_P279_only": one("select count(*) from article_type where source = 'P279'"),
        "untyped_with_item": one("select count(*) from article_type where source = 'none'"),
        "articles_with_primary_type_share": round(typed / rep["articles_total"], 4),
        "articles_under_scaffold": typed - under_uncl, "articles_under_unclassified": under_uncl,
        "primary_rule_counts": dict(by_rule), "alternate_edges": len(alt),
        "classes_folded": int(sum(1 for v in allids if folded[v])), "classes_shown": int(sum(1 for v in allids if not folded[v])),
        "additivity_holds": bool(add_ok), "root_primary_count": PC[THING],
        "top_level": [(label_of(c), name_of(c), PC[c]) for c in sorted(children[THING], key=lambda x: -PC[x])],
        "depth_histogram": {int(k): int(v) for k, v in zip(*np.unique(list(depth.values()), return_counts=True))},
    })
    (out / "report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k not in ("depth_histogram",)}, indent=1, ensure_ascii=False))
    log("done")


if __name__ == "__main__":
    if len(sys.argv) != 7:
        sys.exit(__doc__)
    main(*sys.argv[1:])
