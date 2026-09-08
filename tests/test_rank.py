"""Fast, synthetic-data unit tests for the core recommendation algorithm
(src/graph_pipeline/rank.py). Deliberately does NOT require the raw
hackathon data files -- a reviewer can run this immediately after `pip
install -r requirements.txt`, with no data setup, to verify the core logic
independent of the full held-out accuracy claim (which does need the real
data -- see `python -m src.graph_pipeline.evaluate`, README.md §9).
"""

import pandas as pd
import pytest

from src.graph_pipeline import rank


def test_eligibility_gate_blocks_non_allowed_states():
    assert rank.eligibility_gate("allowed") is True
    assert rank.eligibility_gate("blocked") is False
    assert rank.eligibility_gate("self_excluded") is False
    assert rank.eligibility_gate("") is False


def test_recommend_returns_nothing_when_gate_fails():
    result = rank.recommend(
        recent_nodes=["FIXTURE:a", "FIXTURE:b", "FIXTURE:c"],
        fixture_adjacency_index={},
        popularity_df=pd.DataFrame({"bet_intent_count": [10]}, index=["FIXTURE:z"]),
        protection_state="blocked",
    )
    assert result == {"allowed": False, "reason": "protection_gate", "candidates": []}


def test_cold_start_below_threshold_uses_popularity():
    popularity_df = pd.DataFrame(
        {"bet_intent_count": [30, 20, 10]},
        index=["FIXTURE:x", "FIXTURE:y", "FIXTURE:z"],
    )
    result = rank.recommend(
        recent_nodes=["FIXTURE:a", "FIXTURE:b"],  # only 2 distinct -- below MIN_FIXTURES_FOR_GRAPH_SIGNAL
        fixture_adjacency_index={},
        popularity_df=popularity_df,
        protection_state="allowed",
        top_n=3,
    )
    assert result["allowed"] is True
    assert result["reason"].startswith("cold_start")
    assert [c["fixture"] for c in result["candidates"]] == ["FIXTURE:x", "FIXTURE:y", "FIXTURE:z"]
    assert all(c["source"] == "cold_start_popularity" for c in result["candidates"])


def test_warm_session_uses_graph_cooccurrence_and_never_repeats_seen():
    adjacency = {
        "FIXTURE:a": [{"next_node": "FIXTURE:z", "count": 5}, {"next_node": "FIXTURE:b", "count": 1}],
        "FIXTURE:b": [{"next_node": "FIXTURE:z", "count": 3}],
        "FIXTURE:c": [{"next_node": "FIXTURE:y", "count": 2}],
    }
    popularity_df = pd.DataFrame({"bet_intent_count": [1]}, index=["FIXTURE:w"])
    result = rank.recommend(
        recent_nodes=["FIXTURE:a", "FIXTURE:b", "FIXTURE:c"],  # 3 distinct -- meets the threshold
        fixture_adjacency_index=adjacency,
        popularity_df=popularity_df,
        protection_state="allowed",
        top_n=3,
    )
    assert result["reason"].startswith("fixture_cooccurrence")
    fixtures = [c["fixture"] for c in result["candidates"]]
    # FIXTURE:z scored 5+3=8 from two seen fixtures -- must rank first.
    assert fixtures[0] == "FIXTURE:z"
    # The exclude-set invariant: a warm session must never be recommended a
    # fixture it has already opened (a, b, c are all "seen" here).
    assert not ({"FIXTURE:a", "FIXTURE:b", "FIXTURE:c"} & set(fixtures))


def test_deterministic_tie_break_is_score_desc_then_fixture_id_asc():
    adjacency = {
        "FIXTURE:a": [
            {"next_node": "FIXTURE:zzz", "count": 5},
            {"next_node": "FIXTURE:aaa", "count": 5},
        ],
    }
    result = rank.graph_candidates(
        seen_fixtures={"FIXTURE:a"},
        fixture_adjacency_index=adjacency,
        exclude={"FIXTURE:a"},
        top_n=2,
    )
    # Both candidates tie on score (5) -- fixture_id ascending must win,
    # deterministically, every time this runs (see rank.py's docstring on
    # why this matters: hash-randomized set iteration previously made this
    # non-deterministic between process runs).
    assert [c["fixture"] for c in result] == ["FIXTURE:aaa", "FIXTURE:zzz"]


def test_sport_segmentation_restricts_candidates_to_seen_sports():
    adjacency = {
        "FIXTURE:tennis_a": [
            {"next_node": "FIXTURE:football_x", "count": 10},  # higher score, wrong sport
            {"next_node": "FIXTURE:tennis_b", "count": 1},
        ],
    }
    sport_map = {
        "FIXTURE:tennis_a": "tenis",
        "FIXTURE:tennis_b": "tenis",
        "FIXTURE:football_x": "nogomet",
    }
    popularity_df = pd.DataFrame({"bet_intent_count": [1]}, index=["FIXTURE:tennis_b"])
    result = rank.recommend(
        recent_nodes=["FIXTURE:tennis_a"] * 3,  # only 1 distinct fixture, but repeated -> still cold start
        fixture_adjacency_index=adjacency,
        popularity_df=popularity_df,
        protection_state="allowed",
        top_n=3,
        sport_map=sport_map,
        forced_sport="tenis",
    )
    fixtures = [c["fixture"] for c in result["candidates"]]
    assert "FIXTURE:football_x" not in fixtures
