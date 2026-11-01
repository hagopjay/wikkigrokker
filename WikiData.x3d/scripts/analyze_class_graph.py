#!/usr/bin/env python3
"""Measure the Wikidata class graph above the types of English-linked items.

usage: analyze_class_graph.py CLASS_EDGES.tsv CLASS_LABELS.tsv TYPE_COUNTS.tsv OUT.json

Inputs are the QLever exports (IRIs in angle brackets). Edges point child -> parent
(subclass of). Prints a short report and writes the full statistics as JSON.
"""
import collections
import json
import re
import sys

import networkx as nx

EDGES, LABELS, COUNTS, OUT = sys.argv[1:5]
QID = re.compile(r"Q(\d+)")


def qid(s):
    m = QID.search(s)
    return int(m.group(1)) if m else None


G = nx.DiGraph()
unknown_value_edges = 0
with open(EDGES, encoding="utf-8") as f:
    next(f)
    for line in f:
        c, p = line.rstrip("\n").split("\t")
        qc, qp = qid(c), qid(p)
        if qc is None or qp is None:  # "unknown value" statements arrive as blank nodes
            unknown_value_edges += 1
            continue
        G.add_edge(qc, qp)

labels = {}
with open(LABELS, encoding="utf-8") as f:
    next(f)
    for line in f:
        c, lab = line.rstrip("\n").split("\t", 1)
        labels[qid(c)] = lab.rsplit("@", 1)[0].strip('"')

counts = {}
unknown_value_types = 0
with open(COUNTS, encoding="utf-8") as f:
    next(f)
    for line in f:
        t, n = line.rstrip("\n").split("\t")
        qt = qid(t)
        if qt is None:  # an item whose "instance of" is "unknown value"
            unknown_value_types += int(n)
            continue
        counts[qt] = int(n)

G.add_nodes_from(counts)  # types with no superclass at all
name = lambda q: f"{labels.get(q, '(no English label)')} (Q{q})"

stats = {"nodes": G.number_of_nodes(), "edges": G.number_of_edges(), "types_used_by_articles": len(counts),
         "edges_with_unknown_value_parent": unknown_value_edges,
         "type_assignments_with_unknown_value": unknown_value_types}
stats["nodes_without_english_label"] = sum(1 for n in G if n not in labels)

# Cycles: strongly connected components larger than one node
sccs = [c for c in nx.strongly_connected_components(G) if len(c) > 1]
sccs.sort(key=len, reverse=True)
stats["cycles"] = {
    "components": len(sccs),
    "nodes_in_components": sum(len(c) for c in sccs),
    "largest": [{"size": len(c), "sample": [name(q) for q in sorted(c)[:6]]} for c in sccs[:8]],
}
stats["self_loops"] = nx.number_of_selfloops(G)

# Work on the condensation so that depth and reachability are well defined
C = nx.condensation(G)
member = C.graph["mapping"]  # node -> component id
comp_size = collections.Counter(member.values())

roots = [n for n in C if C.out_degree(n) == 0]
stats["roots"] = len(roots)

# Depth: shortest and longest path (in components) from each class up to a root
order = list(nx.topological_sort(C))  # parents come after children in edge direction child->parent
longest_up = {}
for n in reversed(order):
    longest_up[n] = 0 if C.out_degree(n) == 0 else 1 + max(longest_up[p] for p in C.successors(n))
shortest_up = {}
for n in reversed(order):
    shortest_up[n] = 0 if C.out_degree(n) == 0 else 1 + min(shortest_up[p] for p in C.successors(n))

def distribution(values):
    d = collections.Counter(values)
    return {str(k): d[k] for k in sorted(d)}

stats["depth_shortest_to_root_classes"] = distribution(shortest_up[member[n]] for n in G)
stats["depth_longest_to_root_classes"] = distribution(longest_up[member[n]] for n in G)
tot_assign = sum(counts.values())
stats["type_assignments_total"] = tot_assign
stats["share_of_assignments_by_shortest_depth"] = {
    k: round(v / tot_assign, 4)
    for k, v in sorted(
        collections.Counter(
            {d: sum(counts[t] for t in counts if shortest_up[member[t]] == d) for d in set(shortest_up[member[t]] for t in counts)}
        ).items()
    )
}

# Parents per class
outdeg = collections.Counter(G.out_degree(n) for n in G)
stats["parents_per_class"] = {str(k): outdeg[k] for k in sorted(outdeg)[:12]}
stats["classes_with_2plus_parents"] = sum(v for k, v in outdeg.items() if k >= 2)

# Coverage by root: type assignments that reach each root
root_nodes = {n: [q for q, m in member.items() if m == n] for n in roots}
cover = []
for r, qs in root_nodes.items():
    desc = nx.ancestors(C, r) | {r}  # all components that can reach this root
    reach_nodes = [q for q in G if member[q] in desc]
    a = sum(counts.get(q, 0) for q in reach_nodes)
    cover.append((a, len(reach_nodes), qs[0]))
cover.sort(reverse=True)
stats["top_roots_by_type_assignments"] = [
    {"root": name(q), "type_assignments": a, "classes_below": n, "share": round(a / tot_assign, 4)}
    for a, n, q in cover[:15]
]

# Where the few most-used types sit
top_types = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:25]
stats["top_types"] = [
    {"type": name(q), "articles": n, "shortest_depth": shortest_up[member[q]], "longest_depth": longest_up[member[q]]}
    for q, n in top_types
]
cum = 0
stats["types_needed_for_share"] = {}
ranked = sorted(counts.values(), reverse=True)
for target in (0.5, 0.8, 0.9, 0.95, 0.99):
    cum = 0
    for i, n in enumerate(ranked, 1):
        cum += n
        if cum / tot_assign >= target:
            stats["types_needed_for_share"][str(target)] = i
            break

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(stats, f, indent=1, ensure_ascii=False)
print(json.dumps(stats, indent=1, ensure_ascii=False)[:6000])
