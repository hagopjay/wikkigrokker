#!/usr/bin/env python3
"""Leave-one-out baseline: how well do an article's categories predict its Vital topic?

usage: python3 -I loo_topic_baseline.py DEV_SLICE_DIR OUT_JSON

For every Vital article a and each visible category c of a, count the OTHER Vital articles in c per
Vital topic (a itself is removed from the counts).  a's predicted topic is the topic with the largest
summed count.  Articles whose categories contain no other Vital article get no prediction.
Reports coverage, accuracy among predicted, accuracy over all, and the majority-class baseline.
"""
import json
import sys
from pathlib import Path

import duckdb


def main(ds, out_json):
    ds = Path(ds)
    con = duckdb.connect()
    con.execute("pragma threads=2; pragma memory_limit='3GB'")
    con.execute(f"""create table a as select distinct page_id, topic, level from read_parquet('{ds}/articles.parquet')
                    where page_id is not null and topic is not null""")
    con.execute(f"""create table ac as select page_id, category from read_parquet('{ds}/article_categories.parquet')
                    where not hidden""")
    con.execute("""create table ct as select ac.category, a.topic, count(*) as n
                   from ac join a on a.page_id = ac.page_id group by 1, 2""")
    con.execute("""create table sc as
        select a.page_id, a.topic as gold, a.level, ct.topic as cand,
               sum(ct.n - case when ct.topic = a.topic then 1 else 0 end) as s
        from a join ac on ac.page_id = a.page_id join ct on ct.category = ac.category
        group by 1, 2, 3, 4""")
    con.execute("""create table pred as
        select page_id, gold, level, arg_max(cand, s) as pred, max(s) as ms
        from sc group by 1, 2, 3 having max(s) > 0""")
    n_all = con.execute("select count(*) from a").fetchone()[0]
    maj = con.execute("select topic, count(*) c from a group by 1 order by 2 desc limit 1").fetchone()
    res = {"articles": n_all, "majority_class": {"topic": maj[0], "share": round(maj[1] / n_all, 4)}}

    def block(where):
        tot = con.execute(f"select count(*) from a where {where}").fetchone()[0]
        r = con.execute(f"select count(*), sum((pred = gold)::int) from pred where {where}").fetchone()
        return {"articles": tot, "with_prediction": r[0], "coverage": round(r[0] / tot, 4),
                "accuracy_among_predicted": round(r[1] / r[0], 4), "accuracy_over_all": round(r[1] / tot, 4)}

    res["overall"] = block("true")
    for lv in (1, 2, 3, 4, 5):
        res[f"level_{lv}"] = block(f"level = {lv}")
    # per-topic: 'pred' names the gold topic column 'gold', table 'a' names it 'topic'
    res["by_topic"] = {}
    for (t,) in con.execute("select distinct topic from a order by 1").fetchall():
        tt = t.replace("'", "''")
        tot = con.execute(f"select count(*) from a where topic = '{tt}'").fetchone()[0]
        r = con.execute(f"select count(*), sum((pred = gold)::int) from pred where gold = '{tt}'").fetchone()
        res["by_topic"][t] = {"articles": tot, "coverage": round(r[0] / tot, 4),
                              "recall_over_all": round((r[1] or 0) / tot, 4),
                              "accuracy_among_predicted": round((r[1] or 0) / max(r[0], 1), 4)}
    # most common confusions
    res["top_confusions"] = [(g, p, n) for g, p, n in con.execute(
        "select gold, pred, count(*) from pred where gold <> pred group by 1, 2 order by 3 desc limit 12").fetchall()]
    Path(out_json).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
