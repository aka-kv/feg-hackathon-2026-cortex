"""Node-identity assignment: turns cleaned event-log rows into an
interaction-node label and (optionally) a content-node label.

Two node types, per docs/design-final-sports-graph.md §4:
  - interaction nodes: screens, pages, and actions — small, fixed-ish vocabulary
  - content nodes: fixtures/races/outrights — the "what" a session is looking at

A single row can carry both: e.g. a betslip_add_bet row is an ACTION
interaction-node AND (usually) a fixture content-node at the same instant.

Vectorized, not row-wise `.apply(axis=1)` — the first version of this file
used per-row apply and was the dominant cost in a full pipeline run (tens
of seconds to minutes over 1.4M rows), which would have undercut the whole
"cheap counting, no ML needed" pitch on its own implementation.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from . import config

_CONTENT_PREFIX_TUPLE = tuple(config.CONTENT_ID_PREFIXES)


def extract_page_path(url: str) -> str:
    """Normalize a page_location URL down to just its path, stripping query
    strings and auth tokens (§3.6 rule 7 — these are real, informative
    nodes on www.psk.hr, not noise to discard)."""
    if not url:
        return ""
    path = urlsplit(url).path or "/"
    return path.rstrip("/") or "/"


def _interaction_nodes(df: pd.DataFrame) -> pd.Series:
    event = df["event_name"]
    screen = df["fortuna_screen_name"]
    platform = df["platform"]

    is_action = event.isin(config.ACTION_EVENTS)
    is_history_repeat = is_action & (event == "betslip_add_bet") & (
        df["added_from"] == "betslip_history"
    )
    is_nav = event.isin(config.NAV_EVENTS)
    is_page = event == config.PAGE_EVENT

    # §3.6 rule 7: path extraction only meaningful for page_view rows, but
    # page_location is blank everywhere else anyway, so a Series-level map
    # over the whole column is cheap (fast-paths on empty string) and still
    # far faster than a full-row apply.
    paths = df["page_location"].map(extract_page_path)

    result = np.select(
        [is_history_repeat, is_action, is_nav & (screen != ""), is_nav, is_page],
        [
            "ACTION:betslip_repeat_from_history",
            "ACTION:" + event,
            "SCREEN:" + screen,
            "SCREEN:unknown_" + platform,  # §3.6 rule 2: Android screen_view fallback
            np.where(paths != "", "PAGE:" + paths, "PAGE:unknown_" + platform),
        ],
        default="OTHER:" + event,
    )
    return pd.Series(result, index=df.index)


def _content_nodes(df: pd.DataFrame) -> pd.Series:
    """§3.6 rule 4: widened beyond ufo:mtch: to include ufo:race: and
    ufo:otrt: — these aren't malformed IDs, they're valid racing and
    outright/tournament markets."""
    fid = df["fixture_id"]
    matches = fid.str.startswith(_CONTENT_PREFIX_TUPLE)
    content = ("FIXTURE:" + fid).where(matches)
    return content.replace({"": None})


def resolve_single_event(
    event_name: str,
    fortuna_screen_name: str = "",
    platform: str = "",
    added_from: str = "",
    fixture_id: str = "",
    page_location: str = "",
) -> tuple[str, str | None]:
    """Live-API entry point for a single incoming event. Deliberately reuses
    the exact same vectorized functions the batch pipeline uses (via a
    1-row DataFrame) rather than a second, hand-written implementation —
    two code paths computing "what node is this" would be a correctness
    risk (batch and serving silently drifting apart), which matters more
    here than the small per-call DataFrame construction overhead."""
    row = pd.DataFrame(
        [
            {
                "event_name": event_name,
                "fortuna_screen_name": fortuna_screen_name,
                "platform": platform,
                "added_from": added_from,
                "fixture_id": fixture_id,
                "page_location": page_location,
            }
        ]
    )
    interaction = _interaction_nodes(row).iloc[0]
    content = _content_nodes(row).iloc[0]
    return interaction, (content if pd.notna(content) else None)


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    """Add `interaction_node` and `content_node` columns to a cleaned event
    frame. Does not mutate order — call after clean.clean() has already
    sorted by (session_key, timestamp)."""
    df = df.copy()
    df["interaction_node"] = _interaction_nodes(df)
    df["content_node"] = _content_nodes(df)
    return df
