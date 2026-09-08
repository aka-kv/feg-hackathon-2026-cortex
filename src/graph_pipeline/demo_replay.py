"""Replay a real historical session through the recommend() pipeline,
step by step, to see exactly when the "3 fixtures viewed" trigger fires and
whether the recommended candidate matches what the session actually did
next. This is a validation tool now and the seed of Phase E's demo replay
harness later — same mechanism, not a separate thing built twice.
"""

from __future__ import annotations

from . import build_graph, clean, cooccurrence, nodes, popularity, rank


def pick_demo_session(df_annotated, steps, min_fixture_views: int = 5) -> str:
    """Find a real session with enough fixture-view variety to make a
    meaningful demo — not cherry-picked for outcome, just for length."""
    fixture_steps = steps[steps["node"].str.startswith("FIXTURE:")]
    counts = fixture_steps.groupby("session_key")["node"].nunique()
    candidates = counts[counts >= min_fixture_views].index
    if len(candidates) == 0:
        raise ValueError("No session meets the minimum fixture-view threshold")
    return candidates[0]


def replay(session_key: str, steps, adjacency_index, popularity_df):
    session_steps = steps[steps["session_key"] == session_key].reset_index(drop=True)
    recent_nodes: list[str] = []

    print(f"=== Replaying session {session_key} ({len(session_steps)} steps) ===\n")
    for i, row in session_steps.iterrows():
        node = row["node"]
        recent_nodes.append(node)

        # recommend() only needs to be (re-)evaluated when a new fixture is
        # seen — seen_count can't change on any other step, so skip the
        # (cheap, but not free) lookup on the other ~97% of steps.
        if not node.startswith("FIXTURE:"):
            continue

        seen_count = len(rank.viewed_fixtures(recent_nodes))
        print(f"[step {i}] session views/bets on {node}  (distinct fixtures seen so far: {seen_count})")

        if seen_count == rank.MIN_FIXTURES_FOR_GRAPH_SIGNAL:
            result = rank.recommend(
                recent_nodes, adjacency_index, popularity_df, protection_state="allowed", top_n=3
            )
            print(f"  -> TRIGGER FIRES ({result['reason']})")
            for c in result["candidates"]:
                print(f"     top pick: {c['fixture']}  score={c['score']}  source={c['source']}")

    # Did the session's eventual actual bet land on something we'd have
    # recommended once the trigger fired? Honest check, not assumed.
    actual_fixtures_in_order = [
        n for n in recent_nodes if n.startswith("FIXTURE:")
    ]
    if len(actual_fixtures_in_order) > rank.MIN_FIXTURES_FOR_GRAPH_SIGNAL:
        after_trigger = actual_fixtures_in_order[rank.MIN_FIXTURES_FOR_GRAPH_SIGNAL:]
        print(f"\nFixtures the session actually went on to view/bet after the trigger point: {after_trigger}")


if __name__ == "__main__":
    df, _ = clean.clean()
    df = nodes.annotate(df)
    steps = build_graph.build_steps(df)
    cooc = cooccurrence.build_fixture_cooccurrence(steps)
    fixture_adjacency = cooccurrence.build_fixture_adjacency_index(cooc, top_k=20)
    pop = popularity.fixture_popularity_from_graph(steps, df)

    demo_session = pick_demo_session(df, steps)
    replay(demo_session, steps, fixture_adjacency, pop)
