"""Paths and constants for the sportsbook interaction graph pipeline.

See docs/design-final-sports-graph.md for the design this implements —
every constant below traces back to a specific, verified finding in that
document (section references given inline).
"""

from pathlib import Path

# Raw data lives outside the repo per the hackathon's data rule (never commit
# the provided sample files) — point this at wherever they actually sit.
DATA_DIR = Path(r"C:\Users\Keerthivasan\Desktop\Ai workspace")
EVENT_LOG_PATH = DATA_DIR / "top_sport_users_event_logs.csv"
SB_MOM_PATH = DATA_DIR / "SB_MOM.csv"
SB_PLAYER_PATH = DATA_DIR / "SB_Player.csv"
EPS_OFFERS_PATH = DATA_DIR / "EPS_Offers.csv"

# --- §3.6 rule 1 / rule 7: casino exclusion (7,903 rows total, not 552) ---
CASINO_EVENT_NAME = "casino_game_launch"
CASINO_DOMAIN_MARKER = "casino"  # substring match against page_location, case-insensitive

# --- Event taxonomy (§3.1) ---
ACTION_EVENTS = {"betslip_add_bet", "betslip_placed", "betslip_placed_bet"}
NAV_EVENTS = {"fortuna_screen_view", "screen_view"}
PAGE_EVENT = "page_view"

# --- §3.6 rule 4: content-node identifier pattern, widened beyond ufo:mtch: ---
CONTENT_ID_PREFIXES = ("ufo:mtch:", "ufo:race:", "ufo:otrt:")

# --- Serving/offline tuning ---
TOP_K_NEXT_NODES = 20  # per-node adjacency truncation for the serving index
