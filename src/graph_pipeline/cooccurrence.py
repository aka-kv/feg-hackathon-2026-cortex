"""Fixture-to-fixture co-occurrence — the actual signal "next fixture"
recommendation needs.

Found while validating Phase C on a real replayed session: the 1-hop
interaction-node adjacency (build_graph.py) is right for navigation-flow
reasoning (screen -> screen, screen -> action) but wrong for fixture-level
recommendation, because direct FIXTURE -> FIXTURE edges are almost
nonexistent (there are almost always intermediate screens/actions between
two fixture views), and aggregating through high-traffic common nodes like
ACTION:betslip_add_bet just regresses to overall popularity, not
session-specific signal.

This module builds the standard item-to-item pattern instead: within each
session, if fixture A was first viewed before fixture B, count that as one
occurrence of "A leads to B" — robust to whatever navigation happens in
between, unlike the 1-hop graph.

Implemented as a vectorized pandas self-join, not a per-session Python
double loop — the first version of this file used a nested loop and was
too slow even at hackathon-sample scale (15k sessions), which would have
undercut the whole "counting scales, training doesn't" pitch. This is the
same computation a Spark job would do at production scale: a self-join on
session key, filtered to forward pairs, then a group-by count.
"""

from __future__ import annotations

import pandas as pd


def build_fixture_cooccurrence(steps: pd.DataFrame) -> pd.DataFrame:
    fixture_steps = steps[steps["node"].str.startswith("FIXTURE:")]

    # first occurrence of each fixture per session, in visit order
    first_seen = (
        fixture_steps.groupby(["session_key", "node"])["row_seq"]
        .min()
        .reset_index()
        .rename(columns={"node": "fixture", "row_seq": "first_seq"})
    )

    # Self-join each session's fixtures against themselves, keep only
    # forward-ordered pairs (a's first view precedes b's) — vectorized,
    # no Python-level loop over pairs.
    merged = first_seen.merge(first_seen, on="session_key", suffixes=("_a", "_b"))
    forward_pairs = merged[merged["first_seq_a"] < merged["first_seq_b"]]

    counts = (
        forward_pairs.groupby(["fixture_a", "fixture_b"])
        .size()
        .reset_index(name="count")
    )
    return counts.sort_values("count", ascending=False).reset_index(drop=True)


def build_fixture_adjacency_index(cooccurrence: pd.DataFrame, top_k: int) -> dict:
    adjacency: dict[str, list[dict]] = {}
    for fixture, group in cooccurrence.groupby("fixture_a"):
        top = group.nlargest(top_k, "count")
        adjacency[fixture] = [
            {"next_node": row.fixture_b, "count": int(row.count)}
            for row in top.itertuples(index=False)
        ]
    return adjacency
