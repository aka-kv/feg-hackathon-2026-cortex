# Cortex — Impact Case & Cost-Value Analysis (D3)

FEG Innovation Hackathon 2026 · Challenge 1: Session Quality & Session-to-Action Conversion (PSK / Croatia sportsbook track)

Current, non-superseded version — see `architecture.md` for why this replaces the original casino-era draft.

## 1. Problem

A sportsbook session that browses several fixtures without betting is a session with real, measurable intent that isn't being acted on. The brief's own framing (session quality, session-to-action conversion) asks: can we recognize that intent *during* the session and surface something relevant, without resorting to generic "most popular" content or manufactured urgency?

## 2. Solution, in one paragraph

We didn't just build this and hope it works — we tested it properly. We split real session data 80/20: built the recommendation graph from 80% of sessions, then measured accuracy only on the other 20%, sessions the graph never saw. Out of 16,692 distinct real fixtures a person could be shown, our system's top-3 guess included the fixture they actually went on to bet on **42.1%** of the time. A naive "just show what's popular" approach only gets **3.5%** on the same test — an **~12.1x** improvement, measured on data the model was never shown, not a training artifact.

## 3. Why this, and not something fancier

- **Tested the obvious objection first**: "won't the same popular fixture just get recommended to everyone?" Yes, until we added sport segmentation — verified a real cross-sport contamination bug (a popular football match recommended to 42 non-football sessions), fixed it at zero accuracy cost, verified again (0 violations).
- **Tested a fancier alternative (PMI/lift normalization) and rejected it** — it nearly halved hit rate (0.4162 → 0.2642) because our co-occurrence counts are sparse; normalizing by popularity mostly amplified noise from rare pairs rather than improving diversity. We kept the simpler approach because it measurably worked better, not because it was easier to build.
- **No trained model** — pure counting/co-occurrence, computed offline, served in O(1) time. Zero training cost, zero retraining cadence to maintain, every recommendation carries its own evidence (source + raw score), inspectable rather than a black box.

## 4. Cost analysis

- **Engineering/compute cost**: the entire recommendation mechanism is counting and lookups — no model training, no GPU, no ongoing retraining pipeline beyond periodically rebuilding the graph as new session data arrives (the same offline job that already runs once).
- **Serving cost**: write-time caching means a live read is a single in-memory dict lookup (sub-millisecond, measured), not a per-request graph traversal. The heaviest computation path (a full graph-candidate generation) measured in low single-digit milliseconds even on a warm session.
- **Operational cost**: no persistent user data store to run or secure (see `compliance-note.md` §1) — the in-memory, TTL'd session store has no database to provision, back up, or protect beyond the process's own memory.

## 5. Value — stated carefully, not oversold

The honest version: 42.1% vs 3.5% is a measured, held-out result — a lift in the odds that a relevant recommendation lands, using nothing but real historical co-occurrence. Framed as an assumption-labeled illustration, not a revenue promise (real baseline volumes are FEG's to share post-NDA): if even a modest share of sessions that currently browse without a next-step nudge instead see a relevant, non-pushy "you might like this," and a fraction of those act on it, that is incremental engagement recovered from sessions that were already there — not new acquisition spend, not a discount, not urgency copy. The lift comes from relevance, which is exactly what the brief's guardrail asks for.

## 6. Demonstrated across real, verified scenarios

Three real scenarios were built and verified against raw data, not synthesized: a genuine cold-start (new/guest visitor, popularity fallback, honestly labeled as a mechanism demo since no true first-time visitor exists in a "top users" sample), a minimal-record real returning session (6 distinct fixtures touched, held-out, verified hit), and a heavy-record real returning session (69 distinct fixtures, held-out, verified hit — the same fixture recommended was actually bet on dozens of times across the session, not a single coincidence). See `docs/mentor-briefing-three-users.md` for the full traceable detail on each.

## 7. Scalability

The mechanism scales with data volume, not model complexity — more sessions simply produce a denser, more confident co-occurrence graph, rebuilt by the same offline job. The live serving path (O(1) cached reads, low-millisecond writes) does not get slower as the fixture catalog grows; it was validated end-to-end against the full 16,692-fixture, 15,314-session dataset already provided, not a toy subset.
