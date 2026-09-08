# Mentor Briefing: Three Real Users, Three Real Predictions

FEG Innovation Hackathon 2026 | Threshold — Sportsbook Session Graph

**One correction before anything else**: the headline number is **42.1% hit rate at top-3, chosen out of 16,692 distinct real fixtures (matches/races/markets)** in the dataset — sourced directly from `artifacts/serving_artifact_build_stats.json`, not estimated.

**If asked "are there really 16,692 games?"**: a "fixture" is one real sporting event, not a game title — "Real Madrid vs Barcelona on Aug 15" is one fixture, one tennis match is one fixture, one horse race is one fixture. This sportsbook covers football, tennis, basketball, table tennis, handball, ice hockey, darts, esports and more, across many leagues worldwide. Across a 31-day month, tens of thousands of individual matches globally is the normal scale of a sportsbook's catalog, not a suspicious number. (A closely related, slightly smaller figure, 15,191, is fixtures that had at least one co-occurrence edge with another fixture in the same session — a handful were only ever viewed alone, so they're in the 16,692 total but don't appear in the adjacency graph. Either number is defensible; 16,692 is the more precise "total distinct fixtures" answer.)

## The one-paragraph speech

> "We didn't just build this and hope it works — we tested it properly. We split our real session data 80/20: built the recommendation graph from 80% of sessions, then measured accuracy only on the other 20%, sessions the graph never saw. Out of 16,692 distinct real fixtures a person could be shown, our system's top-3 guess included the fixture they actually went on to bet on 42.1% of the time. A naive 'just show what's popular' approach only gets 3.5% on the same test. That's an 12.1x improvement, and it's not a training artifact — it's measured on data the model was never shown. We're going to walk you through three real examples: a brand-new visitor, a light returning user, and a heavy returning user, all pulled straight from the provided sample, and show you the prediction was made *before* looking at what they actually did next."

## Scenario 1 — Brand-new visitor (mechanism only, no real ID — and here's why)

There is no real "brand-new user" row to show you, and we want to say that plainly rather than fake one: the provided sample file (`top_sport_users_event_logs.csv`) is explicitly a **"top users" sample** — every one of its 92 players is already a heavy user by construction. A genuinely first-time visitor isn't represented in this data at all.

What we can show, honestly: the *mechanism* for this case. When a session has fewer than 3 fixture views, there's no personal graph signal yet, so the system falls back to population-level popularity (the most-bet-on fixtures across everyone) until it has enough signal of its own. This is a real, working code path (`rank.py`'s `cold_start` branch) — just not attachable to a specific real ID, because no such ID exists in the sample.

## Scenario 2 — Minimal-record real user

- **Real ID**: session `1785708852`, player `56fd153546543a43546431026270d46e67652947be5e9c17eda3fee4d970938f`
- **In the held-out test split**: confirmed yes — this session was never used to build the graph that predicted for it.
- **Real workflow**: a short session — 34 total real events, only 6 distinct fixtures touched the whole session.
- **What we did**: rebuilt the graph excluding this session, watched the session's first 3 distinct fixtures viewed (`1ut-0qa`, `1uu-0di`, `1ut-2pi`), asked the system what it would recommend at that exact moment.
- **Prediction**: `1ut-2at`, `1uu-0dh`, `1ut-0qj`
- **What actually happened next, verified from the raw file**: the session went on to view/bet on `1ut-2at` — a direct hit, twice.
- **Proof file**: `artifacts/session_proof_minimal.xlsx` — yellow row is the trigger, green rows are the real hit.
- *(Note: this replaces an earlier example, `1785672096`, which was found to be a hit only by winning a non-deterministic tie in the ranking code — fixed in `rank.py` with an explicit score/fixture-id tie-break, re-verified against the corrected code, and swapped for this genuinely-verified one. Full 10-user list regenerated accordingly — see `docs/design-final-sports-graph.md` Phase E.)*

## Scenario 3 — Heavy-record real user

- **Real ID**: session `1785590016`, player `85587aa8566465386c1dfc8334e63c792a580e00d6330d01b9f0ee713d06346f`
- **In the held-out test split**: confirmed yes.
- **Real workflow**: a long, active session — 6,613 total real events, 69 distinct fixtures touched.
- **What we did**: same method — graph rebuilt excluding this session, first 3 distinct fixtures viewed (`1us-037`, `1uv-01o`, `1uv-01n`).
- **Prediction**: `1uv-039`, `1uv-01p`, `1uv-04n`
- **What actually happened next, verified from the raw file**: the session repeatedly went on to view/bet on both `1uv-039` and `1uv-04n` — dozens of matching rows, not a single coincidence.
- **Proof file**: `artifacts/session_proof_heavy.xlsx`

## Why these two specifically

They were not cherry-picked from nowhere — they're 2 of 10 confirmed genuine hits produced by the same held-out evaluation that generated the 42.1% headline number (`evaluate.py`), chosen specifically to span a small session and a large one, so the mechanism is shown working across very different data volumes, not just one convenient case.

## If asked: "won't the same popular fixture just get recommended to everyone?"

Good question, and we tested it rather than assumed the answer. Before a fix: across the 776 held-out test sessions, one popular football match was the #1 recommendation for 53 of them — but only 11 of those 53 sessions were even watching football. The other 42 were tennis, e-basketball, baseball sessions getting a football match recommended purely because it was popular overall.

We tried the standard textbook fix (normalize by how popular the target fixture already is) and it made things worse, not better: diversity barely improved while accuracy nearly halved (42.1% → 26.4%), because our co-occurrence counts are sparse and that normalization mostly amplifies noise. We rejected it because we tested it, not because it sounded fancier.

The fix that actually worked: restrict recommendations to the same sport(s) the session is already engaged with — using the sport field already in the data (99.7% coverage), not a new algorithm. Result, measured the same way: hit rate went from 41.6% (unsegmented) to **42.1%** (segmented — no cost, in fact very slightly better), cross-sport violations went from 53 sessions to **zero**, and the single most-recommended fixture's share of all top picks dropped from 6.8% to 2.2%. This is now the production configuration — the 42.1% headline number above already includes this fix.

## Business impact — stated carefully, not oversold

The honest version: 42.1% vs 3.5% is a measured, held-out result — an **~12.1x lift** in the odds that a relevant recommendation lands, using nothing but counting real historical co-occurrence, no trained model, no manufactured urgency. Framed as an assumption-labeled illustration (not a revenue promise, since real baseline volumes are FEG's to share post-NDA): if even a modest share of sessions that currently browse without a next-step nudge instead see a relevant, non-pushy "you might like this," and a fraction of those act on it, that is incremental engagement recovered from sessions that were already there — not new acquisition spend, not a discount, not urgency copy. The lift comes from relevance, which is exactly what the brief's guardrail asks for.
