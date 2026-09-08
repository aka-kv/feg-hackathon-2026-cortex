"""Phase B: build the offline interaction-transition graph.

Pipeline: load -> clean (drop casino, fix timestamps, composite session key)
-> annotate (assign interaction/content nodes) -> expand each row into one
or two ordered "steps" -> within each session, count node -> next_node
transitions -> collapse to a compact top-K adjacency table.

This is a pandas job because the hackathon sample is ~1.4M rows — well
within single-machine capacity. Per docs/design-final-sports-graph.md §6,
the production version is the same aggregation logic running on Spark/Flink
over the real event volume; nothing about the math changes, only the engine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import clean, config, nodes

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"


def build_steps(df: pd.DataFrame) -> pd.DataFrame:
    """Expand each cleaned, annotated row into one or two ordered walk
    steps: the interaction node always first, its content node (if any)
    immediately after — same row, same instant, interaction precedes
    content."""
    df = df.reset_index(drop=True)
    df["row_seq"] = df.index

    interaction_steps = pd.DataFrame(
        {
            "session_key": df["session_key"],
            "row_seq": df["row_seq"],
            "order_in_row": 0,
            "node": df["interaction_node"],
        }
    )

    has_content = df["content_node"].notna()
    content_steps = pd.DataFrame(
        {
            "session_key": df.loc[has_content, "session_key"],
            "row_seq": df.loc[has_content, "row_seq"],
            "order_in_row": 1,
            "node": df.loc[has_content, "content_node"],
        }
    )

    steps = pd.concat([interaction_steps, content_steps], ignore_index=True)
    # row_seq already reflects (session_key, timestamp) order from clean.clean();
    # order_in_row only breaks the tie within a single row.
    steps = steps.sort_values(["session_key", "row_seq", "order_in_row"], kind="stable")
    return steps.reset_index(drop=True)


def count_transitions(steps: pd.DataFrame) -> pd.DataFrame:
    """Vectorized: within each session, the next step's node is the edge
    target. No per-session Python loop needed."""
    steps = steps.copy()
    steps["next_node"] = steps.groupby("session_key")["node"].shift(-1)
    edges = steps.dropna(subset=["next_node"])
    edge_counts = (
        edges.groupby(["node", "next_node"]).size().reset_index(name="count")
    )
    return edge_counts.sort_values("count", ascending=False).reset_index(drop=True)


def build_adjacency_index(edge_counts: pd.DataFrame, top_k: int = config.TOP_K_NEXT_NODES) -> dict:
    """Collapse the full edge-count table into the compact, top-K-per-node
    structure that Phase D's serving layer actually looks up — this is the
    whole point of the two-stage design (docs §6): heavy aggregation here,
    O(1) lookups at serving time."""
    adjacency: dict[str, list[dict]] = {}
    for node, group in edge_counts.groupby("node"):
        top = group.nlargest(top_k, "count")
        adjacency[node] = [
            {"next_node": row.next_node, "count": int(row.count)}
            for row in top.itertuples(index=False)
        ]
    return adjacency


def run() -> dict:
    ARTIFACTS_DIR.mkdir(exist_ok=True)

    df, clean_stats = clean.clean()
    df = nodes.annotate(df)

    steps = build_steps(df)
    edge_counts = count_transitions(steps)
    adjacency = build_adjacency_index(edge_counts)

    node_visits = steps["node"].value_counts()

    edge_counts.to_csv(ARTIFACTS_DIR / "edge_counts.csv", index=False)
    node_visits.to_csv(ARTIFACTS_DIR / "node_visit_counts.csv", header=["count"])
    with open(ARTIFACTS_DIR / "adjacency_index.json", "w", encoding="utf-8") as f:
        json.dump(adjacency, f, indent=2)

    build_stats = {
        **clean_stats,
        "total_walk_steps": len(steps),
        "distinct_nodes": steps["node"].nunique(),
        "distinct_edges": len(edge_counts),
        "top_10_nodes_by_visits": node_visits.head(10).to_dict(),
    }
    with open(ARTIFACTS_DIR / "build_stats.json", "w", encoding="utf-8") as f:
        json.dump(build_stats, f, indent=2, default=str)

    return build_stats


if __name__ == "__main__":
    stats = run()
    for k, v in stats.items():
        print(f"{k}: {v}")
