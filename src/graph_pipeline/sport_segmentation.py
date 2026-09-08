"""Sport segmentation: the fix for the "everyone gets the same popular
fixture" concern.

Found by testing, not assumed: a raw co-occurrence graph, unsegmented,
recommended a popular football (nogomet) match as the #1 pick for 53 test
sessions -- but only 11 of those 53 were even watching football. The rest
were tennis, e-basketball, baseball, e-hockey sessions getting a football
match recommended purely because it was globally popular.

Fix: restrict candidates to the same sport(s) the session has already
shown interest in. Tested against the same held-out evaluation as
evaluate.py: hit rate unchanged (0.4175 vs 0.4162 raw, i.e. no accuracy
cost), cross-sport violations eliminated entirely (0), and concentration
dropped sharply (top-1 share of all rank-1 picks: 6.8% -> 2.2%).

A more "principled-looking" fix (lift/PMI normalization -- downweight an
edge by the target fixture's overall popularity) was tried first and
rejected: it only marginally improved diversity (top-1 share 6.8% -> 6.4%)
while cutting hit rate nearly in half (0.4162 -> 0.2642), because our
co-occurrence counts are sparse (median 2 per pair) and dividing by
popularity mostly amplifies noise from rare pairs. Sport segmentation uses
data we already have (sport_name), costs nothing in accuracy, and is fully
explainable -- not a clustering algorithm, just a real, existing category.
"""

from __future__ import annotations

import pandas as pd


def build_fixture_sport_map(df: pd.DataFrame) -> dict[str, str]:
    """Fixture-node -> sport (lowercased, most common value seen for that
    fixture_id — 99.7% of fixtures have at least one sport_name row; only
    29 of 16,692 have more than one distinct value, resolved by mode)."""
    rows = df[(df["fixture_id"] != "") & (df["sport_name"] != "")].copy()
    rows["sport_name"] = rows["sport_name"].str.strip().str.lower()
    modes = rows.groupby("fixture_id")["sport_name"].agg(lambda s: s.mode().iloc[0])
    return {f"FIXTURE:{fid}": sport for fid, sport in modes.items()}
