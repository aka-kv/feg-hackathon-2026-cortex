"""Build the production-equivalent serving artifacts: fixture co-occurrence
adjacency + popularity prior, from ALL available sessions (not the 80/20
split used only for held-out evaluation in evaluate.py).

Persisted to disk so the serving layer (Phase D) loads once at startup
rather than re-running the ~39s pipeline on every process start. This is
the concrete, hackathon-scale stand-in for what a nightly/incremental Spark
job would produce and hand to the serving index in production — same
computation, different trigger and storage.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import clean, config, cooccurrence, nodes, popularity, sport_segmentation
from .build_graph import build_steps

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"


def build_and_persist(top_k: int = config.TOP_K_NEXT_NODES) -> dict:
    ARTIFACTS_DIR.mkdir(exist_ok=True)

    df, clean_stats = clean.clean()
    df = nodes.annotate(df)
    steps = build_steps(df)

    cooc = cooccurrence.build_fixture_cooccurrence(steps)
    fixture_adjacency = cooccurrence.build_fixture_adjacency_index(cooc, top_k=top_k)
    with open(ARTIFACTS_DIR / "fixture_adjacency.json", "w", encoding="utf-8") as f:
        json.dump(fixture_adjacency, f)

    pop = popularity.fixture_popularity_from_graph(steps, df)
    pop.to_csv(ARTIFACTS_DIR / "fixture_popularity.csv")

    # Sport segmentation (see sport_segmentation.py) -- built from the full
    # dataset, not session-split, since sport is static match metadata, not
    # a behavioral signal. Fixes the cross-sport recommendation problem
    # (a popular football match recommended to a tennis session) at no
    # cost to hit rate -- see docs §Phase C for the tested comparison.
    sport_map = sport_segmentation.build_fixture_sport_map(df)
    with open(ARTIFACTS_DIR / "fixture_sport_map.json", "w", encoding="utf-8") as f:
        json.dump(sport_map, f)

    stats = {
        **clean_stats,
        "distinct_fixture_nodes_in_adjacency": len(fixture_adjacency),
        "distinct_fixture_nodes_in_popularity": len(pop),
        "distinct_fixtures_with_known_sport": len(sport_map),
    }
    with open(ARTIFACTS_DIR / "serving_artifact_build_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, default=str)
    return stats


if __name__ == "__main__":
    stats = build_and_persist()
    for k, v in stats.items():
        print(f"{k}: {v}")
