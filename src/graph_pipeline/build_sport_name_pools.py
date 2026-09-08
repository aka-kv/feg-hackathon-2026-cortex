"""Builds artifacts/sport_name_pools.json -- real per-sport match/fixture
names, so the frontend's illustrative naming no longer reuses the same
12-team Croatian football club pool for every sport (the bug reported:
tennis and basketball fixtures showing football club names).

Why this can't be an exact fixture_id -> fixture_name JOIN, verified before
writing this: SB_Player.csv (real settled bets) has PlayerID + placed_date
+ Sport_name_english + fixture_name_english but no fixture_id, no bet ID,
and no precise timestamp -- only a date. Checked the overlap: 89 of our 92
PlayerIDs do appear in SB_Player.csv, but a single player often places bets
on 50-100+ distinct fixtures on a single day, so (PlayerID, date) does not
disambiguate to one fixture. Forcing a 1:1 join on that key would silently
produce WRONG name assignments most of the time, which is worse than an
honest illustrative name -- so this builds real, per-SPORT name pools
instead: every displayed name is a genuine match/fixture name that really
existed in the real data for that real sport, deterministically assigned
to a fixture_id (same collision-free scheme as before), just no longer
claimed to be that specific fixture's actual name.

Primary source: SB_Player.csv's fixture_name_english (huge coverage: covers
every one of our 30 real sport codes except two negligible ones).
Supplementary source: EPS_Offers.csv's match_name (only 5 sports, used to
add extra variety where it maps cleanly).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"
OUT_PATH = ARTIFACTS_DIR / "sport_name_pools.json"
MAX_POOL_SIZE = 500
MIN_HEAD_TO_HEAD_FOR_FILTER = 20  # below this, don't bother filtering for "X - Y" shape

# Our internal sport code (sport_segmentation.py, lowercased Croatian
# sport_name) -> the exact Sport_name_english value used in SB_Player.csv.
# Verified by direct inspection, not guessed -- see docs/design-final-sports-graph.md.
SB_PLAYER_SPORT_MAP = {
    "nogomet": "Football",
    "tenis": "Tennis",
    "enogomet": "eFootball",
    "ekošarka": "eBasketball",
    "košarka": "Basketball",
    "stolni tenis": "Table tennis",
    "rukomet": "Handball",
    "ehokej": "eIce hockey",
    "hokej": "Ice Hockey",
    "odbojka": "Volleyball",
    "baseball": "Baseball",
    "borilački sportovi": "Martial Arts",
    "esport counter strike": "Esport Counter Strike",
    "vaterpolo": "Water Polo",
    "top ponuda": "TOP OFFER",
    "pikado": "Darts",
    "odbojka na pijesku": "Beach Volleyball",
    "američki nogomet": "American Football",
    "rugbi": "Rugby",
    "badminton": "Badminton",
    "futsal": "Futsal",
    "hokej na travi": "Field Hockey",
    "australski nogomet": "Aussies rules",
    "atletika": "Athletics",
    "boks": "Boxing",
    "plivanje": "Swimming",
    "duel": "DUEL",
    "formula 1": "Formula 1",
    # No matching category in SB_Player.csv -- negligible in our own data
    # (1 and 2 fixtures respectively), handled via fallback below.
    "ostali sportovi": None,
    "nogomet na pijesku": None,
}

# EPS_Offers.csv only covers 5 sports -- used as supplementary variety only.
EPS_OFFERS_SPORT_MAP = {
    "nogomet": "Soccer",
    "košarka": "Basketball",
    "tenis": "Tennis",
    "borilački sportovi": "MMA",
    "hokej": "Ice Hockey",
}

# Fallback for the 2 sports with no real pool of their own -- both are
# football variants/edge cases in our data (1 and 2 fixtures total), so
# borrowing the real football pool is a defensible stand-in, disclosed here
# rather than silently invented.
FALLBACK_SPORT = "nogomet"


def _clean_names(series: pd.Series) -> list[str]:
    names = series[series.str.strip() != ""].unique().tolist()
    return sorted(set(n.strip() for n in names))


def _prefer_head_to_head(names: list[str]) -> list[str]:
    """Prefer "X - Y" / "X vs. Y" shaped entries (real head-to-head match
    names) over tournament-header rows that sometimes appear in the same
    column (e.g. "ATP Cincinnati, USA Men Singles 2026"). Only applied when
    there are enough head-to-head entries to not lose real coverage for
    thin sports (individual-event sports like Formula 1/Swimming have no
    "vs" shape at all and must keep everything)."""
    h2h = [n for n in names if " - " in n or " vs" in n or " v " in n]
    return h2h if len(h2h) >= MIN_HEAD_TO_HEAD_FOR_FILTER else names


def main() -> None:
    sb = pd.read_csv(
        config.SB_PLAYER_PATH, dtype=str, keep_default_na=False,
        usecols=["fixture_name_english", "Sport_name_english"],
    )
    eps = pd.read_csv(
        config.EPS_OFFERS_PATH, dtype=str, keep_default_na=False,
        usecols=["match_name", "sport_name"],
    )

    with open(ARTIFACTS_DIR / "fixture_sport_map.json", encoding="utf-8") as f:
        sport_map = json.load(f)
    our_sports = sorted(set(sport_map.values()))

    pools: dict[str, list[str]] = {}
    for code in our_sports:
        names: list[str] = []
        eng = SB_PLAYER_SPORT_MAP.get(code)
        if eng:
            sb_names = _clean_names(sb.loc[sb["Sport_name_english"] == eng, "fixture_name_english"])
            names.extend(_prefer_head_to_head(sb_names))
        eps_sport = EPS_OFFERS_SPORT_MAP.get(code)
        if eps_sport:
            eps_names = _clean_names(eps.loc[eps["sport_name"] == eps_sport, "match_name"])
            names.extend(eps_names)
        names = sorted(set(names))[:MAX_POOL_SIZE]
        pools[code] = names

    for code, names in pools.items():
        if not names:
            pools[code] = pools.get(FALLBACK_SPORT, [])[:MAX_POOL_SIZE]

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(pools, f, ensure_ascii=False)

    print(f"Wrote {OUT_PATH}")
    for code in our_sports:
        fallback_note = " (fallback: borrowed from nogomet)" if not SB_PLAYER_SPORT_MAP.get(code) and not EPS_OFFERS_SPORT_MAP.get(code) else ""
        print(f"  {code:25s} {len(pools[code]):5d} real names{fallback_note}")


if __name__ == "__main__":
    main()
