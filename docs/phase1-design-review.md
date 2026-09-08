> **SUPERSEDED.** This document predates the post-mentor-round pivot (sportsbook-only, graph-based next-item recommendation). Current source of truth: `design-final-sports-graph.md`. Kept for reference only.

# Threshold — Design & Architecture

FEG Innovation Hackathon 2026 | Challenge 1 — Session Quality & Session-to-Action Conversion
Phase 1 Design Review: problem framing, approach lock, tech-stack decisions, task split

---

## 1. Executive Summary

Threshold is a real-time layer that diagnoses *why* a session paused at its moment of highest intent — a casino deposit, a bonus claim, a sportsbook bet slip — before deciding whether to help the user across, or to step back. Diagnosis first, intervention second. "Do nothing" is a legitimate, structural output of the system, not a failure state.

We didn't start from the brief. We started from the sample data FEG gave us, and two of the four hidden tables inside the trend spreadsheets changed our own framing before we ever wrote a line of the pitch. That evidence is below, because it's the reason the rest of this design looks the way it does.

## 2. The Problem, In FEG's Own Data

We found something in the provided data that reframes the brief: **two hidden tables buried past the first sheet in `hackathon_sportsbook_trends.xlsx` and `hackathon_casino_trends.xlsx`**, containing FEG's own official, population-level baselines for two of the brief's named metrics — Session Conversion Rate and Time to First Action — broken out by market, including Croatia.

| Metric | Sportsbook — Croatia (HTK-CRO), Aug 2026 | Casino — PSK market, Aug 2026 |
|---|---|---|
| Session Conversion Rate | 24.1% of sessions place a bet (75.9% place zero) | 43–50% of sessions launch a game |
| Time to First Action | median 377s — **mean 900s** | 25–55 sec to first game launch |

Two things follow directly from this table, before we touch anything else:

- **Sportsbook's mean time-to-first-bet is 2.4x its median.** Most people who bet, bet quickly. A real tail takes far longer — and that gap, sitting in FEG's own numbers, is hesitation. Nothing assumed, nothing sampled.
- **Casino's front door already works.** 43–50% of sessions launch a game in under a minute. The brief's "discovery friction" framing fits the static, identical-for-everyone lobby we confirmed by pulling up `casino.psk.hr` directly (same fixed collections — Favoriti, Nove Igre, Buy Bonus — regardless of who's looking) — but it doesn't fit raw conversion, which is already healthy.

So the real, current leak in casino isn't "will you try a game." It's one step deeper: **does browsing a game turn into a real-money commitment.** No organizer metric measures that step today. That gap — not a bigger drop-off number, but an unmeasured one — is why our design leads with casino, and why we're proposing a new metric to cover it (§3).

We also looked at a second, smaller data source — real anonymized click-by-click session logs for a sample of ~90 active players — to understand the *mechanism* behind the hesitation (what specifically happens in the seconds before someone abandons a deposit or a bet slip). We keep that evidence separate and clearly labeled throughout our documentation, because it's a small, engaged-user sample, not a population baseline — it tells us *how* hesitation happens, the trend tables tell us *how much*.

## 3. Our Design Direction

**The core idea:** at the exact moment a session pauses right before confirming — a deposit, a bonus claim, a bet — the system runs a real-time check and routes the pause into exactly one of four responses:

| Branch | What's happening | What the system does |
|---|---|---|
| Informational uncertainty | Genuine confusion — bonus wagering terms unclear, odds moved unexpectedly | Explain it plainly, grounded in the real terms/odds data, right then |
| UI / flow friction | Something on screen is actually broken or slow | Fix the flow itself — no message, no explanation needed |
| Interruption | Session gap, app backgrounded, KYC step-up | Quietly restore where they left off, only if they return on their own |
| Behavioral red flag | Deposit velocity spike, stake escalation vs. their own baseline, or a near-miss-triggered chase | **Disengage entirely** — no offer, no message urging continuation. Surface a limit-setting or reality-check tool instead |

The branch selection *is* the compliance design, not a layer bolted on top of it: the red-flag branch is a hard, unreachable code path for the offer/LLM logic — not a filtered message, a path the system structurally cannot take.

**Where casino leads:** the primary scenario is the deposit/bonus-claim confirm step, because that's where the organizers asked us to focus, and where §2's unmeasured gap and the real compliance risk both live. Sportsbook's bet-slip flow runs the same four branches as a secondary scenario, proving the mechanism generalizes rather than being a one-off script for a single screen.

**One addition worth flagging directly, because we'd rather say it first than have it discovered:** the red-flag branch's near-miss-triggered chasing signal — a spin lands one symbol short of a win and is immediately followed by a stake increase — has no supporting field in the provided sample data (no per-spin outcome exists anywhere in the tables). We simulate it for the demo, and we say so everywhere it appears. It's included because it's a well-documented driver of harmful play that, per our own review of what's shipped today (BetBuddy, GameScanner, and similar tools), nothing currently distinguishes in real time — it's the sharpest original claim in the design, and also the one place a sharp question should land, so we're volunteering the caveat rather than waiting to be asked.

**New measurement we're proposing**, directly answering the brief's "better ways to measure session quality" ask:

- **Real-Money Commitment Rate** — share of deposit/bonus-intent sessions that convert to real-money play in the same session. Fills the exact gap identified in §2.
- **Resolved Hesitation Rate** — share of confirm-step pauses resolved through genuine information, not pressure.
- **Informed Action Score** — whether the eventual action followed a resolved moment of uncertainty, rather than raw conversion.

**Making the mechanism visible, not just claimed:** because this whole design lives in a moment that's normally invisible to a user (and to a judge watching a demo), three things turn it into something provable rather than asserted:

1. A **live reasoning-trace panel** narrates the classifier's actual checks as they run — the same checks, rendered to screen, not a separate explanation layer.
2. A **judge-interactive mode** lets someone in the room drive the bet slip or deposit screen themselves and watch the system respond to their own real behavior, not a canned replay.
3. An **"ask why" assistant** on the confirm screen answers, live, grounded in the same real data as the automatic explanation — and visibly refuses to upsell if asked something red-flag-adjacent, proving the guardrail is structural.

## 4. Tech-Stack Decisions

We deliberately simplified the infrastructure for a two-day build, and we'd rather explain that choice than oversell it.

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js/React, self-built mimic of PSK's real navigation and casino lobby | No access to instrument the live site; a self-built mimic replaying real session data is fully within our control for a live demo |
| Backend | FastAPI, single service | Fastest reliable path to a working demo in two days |
| Session state | In-memory / SQLite | No infra setup risk; TTL behavior described as the production intent rather than built out at hackathon scale |
| Classifier | Explainable rules + a small gradient-boosted model on named features | Every decision can show exactly which check fired — supports explainability by construction, not as an afterthought |
| Explanation layer | One real LLM call per resolvable case, grounded in real bonus terms / odds-movement data | We chose to prove this works live rather than mock it — it's the piece worth the most scrutiny |
| Guardrail | Banned-phrase filter on generated text, plus a hard code path that the red-flag branch never reaches the LLM/offer logic at all | Enforced structurally and demonstrable in code, not just described in a slide |
| Orchestration | An explicit state machine, not an agent framework | A visible switch between four named branches is a *better* explainability story than a black-box agent loop |

## 5. Task Split

*(placeholder structure — map to your actual team)*

| Workstream | Covers |
|---|---|
| Data & classifier | Tiered evidence (official baselines vs. sample), the four-branch rule logic, the real odds-movement join |
| Backend & orchestration | FastAPI service, session replay engine, LLM explanation layer + guardrails |
| Frontend & demo experience | Mimic UI, reasoning-trace panel, judge-interactive mode, "ask why" assistant |
| Docs & compliance | Impact case, compliance note, README, keeping the official-vs-sample distinction consistent everywhere |

## 6. What Happens After This Review

Hackathon build: working prototype on the provided sample data, both scenarios, all four branches shown at least once, live. Pilot (post-shortlist): real baseline data under NDA replacing our stated assumptions, genuine holdout measurement, responsible-gambling thresholds calibrated jointly with FEG's compliance team. Production: phased rollout alongside FEG's existing checkout and personalization stack — this design was never intended to replace it.
