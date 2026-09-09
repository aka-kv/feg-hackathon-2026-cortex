"""Held-out evaluation of the Phase C recommendation mechanism.

The single-session check in demo_replay.py was a sanity check, not real
validation — it built the co-occurrence graph from ALL sessions, including
the one it then evaluated against, which is leakage (the session's own
fixture pairs contributed to the counts used to "predict" it). This script
fixes that with a proper train/test split at the session level: the graph
and popularity prior are built only from train sessions; hit-rate is
measured only on test sessions the graph never saw.

Metric: hit@3 — for each qualifying test session, does the recommend()
call's top-3 output (fired at the mentor's 3-distinct-fixtures trigger)
include any fixture the session actually goes on to view/bet on
afterward? Compared against a popularity-only baseline on the exact same
trigger points, to see whether the graph signal adds real lift over "just
recommend whatever's globally popular."
"""

from __future__ import annotations

import random

import pandas as pd

from . import clean, cooccurrence, nodes, popularity, rank, sport_segmentation
from .build_graph import build_steps

TEST_FRACTION = 0.2
RANDOM_SEED = 42
MIN_DISTINCT_FIXTURES = MIN_FOR_TRIGGER = rank.MIN_FIXTURES_FOR_GRAPH_SIGNAL + 1


def split_sessions(session_keys: list[str], test_fraction: float, seed: int) -> tuple[set, set]:
    keys = sorted(session_keys)  # sort first for determinism, then shuffle with a fixed seed
    rng = random.Random(seed)
    rng.shuffle(keys)
    n_test = int(len(keys) * test_fraction)
    return set(keys[n_test:]), set(keys[:n_test])  # train, test


def first_seen_fixtures_per_session(steps: pd.DataFrame) -> pd.DataFrame:
    fixture_steps = steps[steps["node"].str.startswith("FIXTURE:")]
    return (
        fixture_steps.groupby(["session_key", "node"])["row_seq"]
        .min()
        .reset_index()
        .rename(columns={"node": "fixture", "row_seq": "first_seq"})
        .sort_values(["session_key", "first_seq"])
    )


def evaluate(top_n: int = 3, rerank_shrinkage_k: float = rank.RERANK_SHRINKAGE_K) -> dict:
    df, _ = clean.clean()
    df = nodes.annotate(df)
    steps = build_steps(df)

    all_sessions = steps["session_key"].unique().tolist()
    train_keys, test_keys = split_sessions(all_sessions, TEST_FRACTION, RANDOM_SEED)

    train_steps = steps[steps["session_key"].isin(train_keys)]
    train_df = df[df["session_key"].isin(train_keys)]

    cooc = cooccurrence.build_fixture_cooccurrence(train_steps)
    fixture_adjacency = cooccurrence.build_fixture_adjacency_index(cooc, top_k=20)
    pop = popularity.fixture_popularity_from_graph(train_steps, train_df)
    # Built from the FULL dataset, not train-only: a fixture's sport is
    # static match metadata (which sport it belongs to), not a behavioral
    # signal derived from what any session did -- using it doesn't leak
    # anything about a test session's future actions, unlike the
    # co-occurrence counts above, which must stay train-only. Building this
    # from train_df alone was tried first and found to matter: it lowers
    # coverage for niche fixtures whose only sport-labeled rows happen to
    # fall in the test split, which let cross-sport violations slip through
    # via the intentional "no known sport -> don't restrict" fallback.
    sport_map = sport_segmentation.build_fixture_sport_map(df)

    fseq = first_seen_fixtures_per_session(steps)
    fseq_test = fseq[fseq["session_key"].isin(test_keys)]

    results = []
    for session_key, group in fseq_test.groupby("session_key"):
        fixtures_in_order = group["fixture"].tolist()
        if len(fixtures_in_order) < MIN_FOR_TRIGGER:
            continue

        seen_at_trigger = fixtures_in_order[: rank.MIN_FIXTURES_FOR_GRAPH_SIGNAL]
        future_fixtures = set(fixtures_in_order[rank.MIN_FIXTURES_FOR_GRAPH_SIGNAL :])

        # System = sport-segmented (production). Baseline = plain global
        # popularity, deliberately NOT sport-segmented, so the comparison
        # stays "our real system" vs "naive popularity," not two segmented
        # variants against each other.
        system_result = rank.recommend(
            seen_at_trigger, fixture_adjacency, pop, protection_state="allowed",
            top_n=top_n, sport_map=sport_map, rerank_shrinkage_k=rerank_shrinkage_k,
        )
        system_candidates = {c["fixture"] for c in system_result["candidates"]}
        system_sources = [c["source"] for c in system_result["candidates"]]

        baseline_candidates = {
            c["fixture"]
            for c in rank.popularity_candidates(pop, set(seen_at_trigger), top_n)
        }

        cross_sport_violation = any(
            sport_map.get(c) not in {sport_map.get(f) for f in seen_at_trigger}
            for c in system_candidates
            if c in sport_map
        )

        results.append(
            {
                "session_key": session_key,
                "system_hit": bool(system_candidates & future_fixtures),
                "baseline_hit": bool(baseline_candidates & future_fixtures),
                "used_graph_signal": "fixture_cooccurrence" in system_sources,
                "cross_sport_violation": cross_sport_violation,
            }
        )

    results_df = pd.DataFrame(results)
    n = len(results_df)
    if n == 0:
        return {"error": "no qualifying test sessions"}

    graph_backed = results_df[results_df["used_graph_signal"]]

    genuine_hits = results_df[results_df["used_graph_signal"] & results_df["system_hit"]]
    example_session_keys = genuine_hits["session_key"].head(10).tolist()

    return {
        "_example_genuine_hit_sessions_for_demo_material": example_session_keys,
        "train_sessions": len(train_keys),
        "test_sessions": len(test_keys),
        "qualifying_test_sessions": n,
        "system_hit_rate@3": round(results_df["system_hit"].mean(), 4),
        "popularity_baseline_hit_rate@3": round(results_df["baseline_hit"].mean(), 4),
        "sessions_with_graph_signal_at_trigger": len(graph_backed),
        "system_hit_rate@3_when_graph_signal_present": (
            round(graph_backed["system_hit"].mean(), 4) if len(graph_backed) else None
        ),
        "cross_sport_violations": int(results_df["cross_sport_violation"].sum()),
    }


if __name__ == "__main__":
    stats = evaluate()
    for k, v in stats.items():
        print(f"{k}: {v}")
