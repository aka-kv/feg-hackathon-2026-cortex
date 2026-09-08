"""In-memory, TTL'd session state — no persistent profile, no database.

Matches docs/design-final-sports-graph.md §5's data-minimization stance and
the original architecture note (30-minute inactivity TTL): a session's node
history exists only while the session is active, is never written to disk,
and vanishes once expired. TTL is enforced on read as well as via the sweep
— a stale entry must never be served just because cleanup hasn't run yet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

DEFAULT_TTL_SECONDS = 30 * 60  # 30 minutes, matching the stated production intent


@dataclass
class SessionState:
    nodes: list[str] = field(default_factory=list)
    last_seen: float = field(default_factory=time.time)
    protection_state: str = "allowed"
    # Write-time cache: computed the instant a click arrives (see
    # app.py's /v1/events), not on demand when the home page asks for it.
    # A home-page read is then a single dict lookup, not a recomputation.
    cached_recommendation: dict | None = None


class SessionStore:
    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, SessionState] = {}

    def _is_expired(self, state: SessionState, now: float) -> bool:
        return (now - state.last_seen) > self.ttl_seconds

    def ensure_session(self, session_key: str, protection_state: str | None = None) -> SessionState:
        """Create the session entry if it doesn't exist yet (or has
        expired), WITHOUT requiring any nodes -- a freshly logged-in,
        zero-interaction session is a legitimate state (cold start), not
        the same thing as "no session exists." Login flows must call this
        (directly, or via add_nodes with an empty/non-empty list) before
        caching a recommendation, or the cache write silently no-ops
        against a session that was never created."""
        now = time.time()
        state = self._sessions.get(session_key)
        if state is None or self._is_expired(state, now):
            state = SessionState()
        state.last_seen = now
        if protection_state is not None:
            state.protection_state = protection_state
        self._sessions[session_key] = state
        return state

    def add_nodes(self, session_key: str, node_list: list[str], protection_state: str | None = None) -> SessionState:
        state = self.ensure_session(session_key, protection_state)
        state.nodes.extend(node_list)
        return state

    def set_cached_recommendation(self, session_key: str, recommendation: dict) -> None:
        state = self._sessions.get(session_key)
        if state is not None:
            state.cached_recommendation = recommendation

    def get_protection_state(self, session_key: str) -> str:
        state = self._sessions.get(session_key)
        return state.protection_state if state else "allowed"

    def get_cached_recommendation(self, session_key: str) -> dict | None:
        """The O(1) read path -- no graph lookup, no ranking, just this.

        Checks whether the SESSION is valid (exists, not expired) -- not
        whether it has any nodes yet. A freshly logged-in cold-start session
        has zero nodes and a perfectly valid cached popularity
        recommendation; treating "zero nodes" as "no session" was a real
        bug that made every fresh login's first Home-page view incorrectly
        show "no session," found by actually running the exact login ->
        immediate Home-page-view sequence end to end, not by inspection."""
        now = time.time()
        state = self._sessions.get(session_key)
        if state is None or self._is_expired(state, now):
            if state is not None:
                del self._sessions[session_key]
            return None
        return state.cached_recommendation

    def get_recent_nodes(self, session_key: str) -> list[str]:
        """TTL is checked here, not just in the sweep — an expired session
        must never silently return stale nodes."""
        now = time.time()
        state = self._sessions.get(session_key)
        if state is None or self._is_expired(state, now):
            if state is not None:
                del self._sessions[session_key]
            return []
        return state.nodes

    def sweep_expired(self) -> int:
        """Explicit cleanup pass — call periodically (e.g. from a
        background task) to bound memory; correctness doesn't depend on
        this running promptly, since get_recent_nodes() re-checks TTL
        itself, but memory would grow unbounded without it."""
        now = time.time()
        expired = [k for k, s in self._sessions.items() if self._is_expired(s, now)]
        for k in expired:
            del self._sessions[k]
        return len(expired)

    def active_session_count(self) -> int:
        return len(self._sessions)
