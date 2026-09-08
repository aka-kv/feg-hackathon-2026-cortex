"""Draw an actual picture of the graph -- what a graph-database browser
view (e.g. Neo4j Browser) would show, but generated offline with
networkx + matplotlib (no CDN, no server, nothing that can fail at a venue
with bad wifi).

Two panels:
  LEFT  -- the exact verified example's neighborhood (the 4 fixtures from
           session_proof.xlsx plus their immediate co-occurrence neighbors),
           so it's traceable back to a concrete, checkable claim.
  RIGHT -- a zoomed-out view of the top 40 most-connected fixtures across
           the whole dataset, so it's clear this is a real graph at scale,
           not just one hand-picked example.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx

from . import cooccurrence, popularity
from .build_graph import build_steps
from .clean import clean
from .nodes import annotate

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"

VERIFIED_EXAMPLE = ["1us-1s3", "1us-1uu", "1us-2c5", "1us-1qe"]


def short(fid: str) -> str:
    return fid.replace("ufo:mtch:", "").replace("ufo:race:", "R:").replace("ufo:otrt:", "O:")


def build_local_neighborhood_graph(cooc, center_ids: list[str], hops: int = 1) -> nx.DiGraph:
    center_nodes = {f"FIXTURE:ufo:mtch:{c}" for c in center_ids}
    frontier = set(center_nodes)
    included = set(center_nodes)
    for _ in range(hops):
        next_frontier = set()
        for node in frontier:
            neighbors = cooc[(cooc["fixture_a"] == node)]["fixture_b"].tolist()
            next_frontier.update(neighbors)
        included |= next_frontier
        frontier = next_frontier

    sub = cooc[cooc["fixture_a"].isin(included) & cooc["fixture_b"].isin(included)]
    g = nx.DiGraph()
    for row in sub.itertuples(index=False):
        g.add_edge(row.fixture_a, row.fixture_b, weight=row.count)
    return g, center_nodes


def build_top_connected_graph(cooc, top_n: int = 40) -> nx.DiGraph:
    degree = cooc.groupby("fixture_a")["count"].sum().add(
        cooc.groupby("fixture_b")["count"].sum(), fill_value=0
    )
    top_nodes = set(degree.nlargest(top_n).index)
    sub = cooc[cooc["fixture_a"].isin(top_nodes) & cooc["fixture_b"].isin(top_nodes)]
    g = nx.DiGraph()
    for row in sub.itertuples(index=False):
        g.add_edge(row.fixture_a, row.fixture_b, weight=row.count)
    return g


def draw(g: nx.DiGraph, ax, title: str, highlight: set[str] | None = None):
    if g.number_of_nodes() == 0:
        ax.set_title(title + " (no edges found)")
        ax.axis("off")
        return
    pos = nx.spring_layout(g, seed=42, k=0.9)
    highlight = highlight or set()
    node_colors = ["#ff6b6b" if n in highlight else "#4f7cff" for n in g.nodes()]
    weights = [g[u][v]["weight"] for u, v in g.edges()]
    max_w = max(weights) if weights else 1

    nx.draw_networkx_edges(
        g, pos, ax=ax, arrows=True, arrowsize=8,
        width=[0.5 + 2.5 * (w / max_w) for w in weights],
        edge_color="#8a91b3", alpha=0.6,
    )
    nx.draw_networkx_nodes(g, pos, ax=ax, node_color=node_colors, node_size=380, edgecolors="white", linewidths=0.6)
    labels = {n: short(n.replace("FIXTURE:", "")) for n in g.nodes()}
    nx.draw_networkx_labels(g, pos, labels=labels, ax=ax, font_size=7, font_color="white")
    ax.set_title(title, color="white", fontsize=11)
    ax.axis("off")


def main():
    df, _ = clean()
    df = annotate(df)
    steps = build_steps(df)
    cooc = cooccurrence.build_fixture_cooccurrence(steps)

    local_g, center_nodes = build_local_neighborhood_graph(cooc, VERIFIED_EXAMPLE, hops=1)
    top_g = build_top_connected_graph(cooc, top_n=40)

    fig, axes = plt.subplots(1, 2, figsize=(18, 9), facecolor="#0f1320")
    for ax in axes:
        ax.set_facecolor("#0f1320")

    draw(
        local_g, axes[0],
        f"Verified example neighborhood\n({local_g.number_of_nodes()} nodes, {local_g.number_of_edges()} edges) -- red = the 4 checkable fixtures",
        highlight=center_nodes,
    )
    draw(
        top_g, axes[1],
        f"Top 40 most-connected fixtures, whole dataset\n({top_g.number_of_nodes()} nodes, {top_g.number_of_edges()} edges)",
    )

    fig.suptitle(
        "This is the actual fixture co-occurrence graph, built from real session data (top_sport_users_event_logs.csv)\n"
        "Nodes = matches; edge A→B = \"sessions that saw A also went on to see B\", edge thickness = how often",
        color="white", fontsize=12,
    )
    out_path = ARTIFACTS_DIR / "graph_visualization.png"
    fig.savefig(out_path, dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
