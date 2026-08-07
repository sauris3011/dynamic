# Dynamic Pricing Engine

AI-driven dynamic pricing for retail. Five-agent pipeline, deterministic
optimization, compliance veto, and banded autonomy.

Built against [prd.md](prd.md) v1.1 — the source of truth. Phase 1 architecture
decisions are D1–D15; departures from them are logged in
[docs/DEVIATIONS.md](docs/DEVIATIONS.md).

---

## Quick start

```bash
# Windows
startup.bat

# macOS / Linux
./startup.sh
```

The script creates the venv, installs dependencies, seeds the synthetic dataset,
runs pre-flight checks, and starts both services.

| Service | URL | Role |
|---|---|---|
| Commerce Service | http://127.0.0.1:8001/docs | Enterprise system of record |
| Pricing Platform | http://127.0.0.1:8000/docs | AI solution layer |
| Web UI | http://127.0.0.1:5173 | React client — talks only to :8000 |

Manual equivalent:

```bash
python -m venv .venv && .venv\Scripts\activate      # or source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                              # or cp
set PYTHONPATH=%CD%                                 # or export PYTHONPATH=$PWD
python -m commerce.seed
python -m pricing.scripts.preflight
python -m uvicorn commerce.main:app --port 8001
python -m uvicorn pricing.main:app  --port 8000
```

---

## The design in one paragraph

Price optimization is mathematics, and compliance enforcement is a legal
control. Neither belongs inside a language model. So the numbers come from
deterministic Python (`pricing/analytics/`), the rules hold a hard veto
(`pricing/rules/`), and the LLM's job is to explain the decision in language a
pricing manager can act on and a compliance officer can audit. **The whole
pipeline runs with the LLM gateway switched off** — narration degrades to
deterministic text and every price is unchanged.

### The three-layer modelling stack

This ordering is a requirement, not a preference (PRD §4.7):

```
sales history -> elasticity regression  (CAUSAL: price -> demand)
              -> Monte Carlo            (UNCERTAINTY: distributions around it)
              -> constrained optimizer  (DECISION: best feasible price)
```

Monte Carlo must sample the **estimated elasticity**, never historical sales
variance. Historical variance says how demand *has fluctuated*; it says nothing
about how demand *responds to a price not yet set*. Sampling it yields a tight,
confident, meaningless interval.

### Autonomy: enforcement is absolute, approval is graduated

| Band | Condition | Behaviour |
|---|---|---|
| Auto-approve | High confidence, small delta, clean compliance, low variance, stable | Pushes without a human (in Assisted/Autonomous) |
| Review | Any auto-approve condition unmet | Human queue |
| Escalate | Compliance breach, margin proximity, high volatility, no usable elasticity, oscillation | Blocked; compliance breaches can never be overridden |

Modes: **Supervised** (default — nothing auto-pushes), Assisted, Autonomous.
A kill switch stops the loop, cancels pending auto-approvals, and reverts to
Supervised in one action.

---

## Layout

```
commerce/          Enterprise system of record (port 8001, commerce.db)
  routes/          catalog, sales, inventory, prices, market, evaluation
  validation.py    Independent re-validation — rejects what the platform sends
  seed.py          Synthetic data with known ground-truth elasticity

pricing/           AI solution layer (port 8000) — owns no business data
  analytics/       Deterministic. No LLM anywhere in this package.
    elasticity.py    Layer 1 — causal demand model
    montecarlo.py    Layer 2 — uncertainty
    optimizer.py     Layer 3 — constrained decision
    price_points.py  Retail price ladder (integer cents)
    baseline.py      Rule-based comparator for PRD 9.3
    stability.py     Oscillation, damping, convergence
    quality.py       Data quality gate
    refinement.py    Bounded elasticity/dispersion updates (the learning loop)
  rules/           engine.py (compliance veto), bands.py (autonomy)
  llm/             registry.py (boot probe), grounded.py (ONLY LLM entry point)
  rag/             ChromaDB store + no-download embedding fallback
  pipeline/        Five stages, graph.py (LangGraph), persistence, execution,
                   narration, rationale.py (deterministic fallback text)
  chat/            Analyst assistant — routing (model + keyword fallback),
                   fact builders (product, history, what-if, run analysis,
                   platform), answer composition, state-aware suggestions
  feeds/           Third-party competitor feed (pluggable)
  routes/          runs, recommendations, loop, rag, simulation, feedback,
                   metrics, ops, chat, a2a
  services/        loop, feedback (readback + refinement), simulation,
                   metrics, scoring (ground-truth harness)
  core/            logging (redaction), tls, telemetry (Langfuse + JSONL sink)

ui/                React + Vite + Tailwind, port 5173
  src/components/  Header (monitor, mode, kill switch), Sidebar, RAG panel,
                   settings drawer, band indicators, distribution charts,
                   chat terminal (answer plus the evidence behind it)
  src/routes/      Run console, review queue, recommendation detail,
                   compliance & audit, simulation, metrics, loop control
  src/lib/         api client (the only network surface), hooks, formatting

tests/             pytest suite — analytics, compliance, bands, feedback loop,
                   commerce idempotency, golden path, architecture invariants
```

---

## What is verified working

Confirmed by execution, not assertion:

- **Elasticity is recoverable** from the synthetic history: MAE 0.233, bias
  +0.003, correlation 0.873 across 485 SKUs. The naive uncontrolled estimator
  shows bias −0.453 — promotions cut price *and* add a visibility lift, so
  omitting the promo control over-states elasticity and causes over-discounting.
- **End-to-end run**: 98 SKUs priced in 0.62s (NFR-006 budget is 300s).
- **Supervised mode does not auto-push.** Verified: all recommendations stayed
  `pending`.
- **Approve → push changes the system of record.** Verified: 16.49 → 14.99 in
  `commerce.db`.
- **Idempotency**: re-pushing a batch replays the result and writes one
  `price_history` row, not two.
- **Independent re-validation**: below-cost, implausible-jump, and unknown-SKU
  pushes all rejected by the Commerce Service on its own terms.
- **Override re-validation**: a manual £0.05 price returns HTTP 409.
- **Graceful degradation**: full run completes with the gateway unreachable.
- **RAG**: ingest, embed, and retrieve working; a margin query correctly ranks
  the pricing policy above market trends.
- **Beats the conventional pricer against ground truth.** Mean deviation from
  the theoretical optimum: **9.13%** for the AI against **15.03%** for the
  rule-based baseline, on identical SKUs and identical data. High-confidence
  SKUs land at 5.07% — inside the PRD 9.1 target of 10%.
- **The loop learns.** Mean absolute forecast error narrowed from 22.5% to 13.5%
  across successive measured outcomes, and run *n+1* demonstrably starts from
  the estimate run *n* refined.
- **Refinement stays bounded.** Maximum observed cumulative adjustment 0.10
  against a 0.75 cap, with every step in the audit log.
- **The analyst assistant answers with the gateway down.** Every intent —
  product, trading history, what-if, run analysis, platform — returns computed
  evidence with no model reachable, and a chat projection matches
  `POST /api/simulate` on the same candidate grid because it *is* the same
  engine.
- **173 of 175 tests pass** (one skipped, one known failure: `llm/grounded.py`
  is 413 lines against the 400-LOC invariant). Passing includes the rest of the
  architecture invariants: the platform never imports `commerce`, only
  `grounded.py` constructs a chat model, no LLM import reaches `analytics/`,
  `rules/` or the push path, no scattered `verify=False`, no emoji in the UI,
  and no gateway credential anywhere in the client.

Reproduce with `pytest` and the `/docs` OpenAPI pages.

```bash
pytest -q                       # 175 tests
npm --prefix ui run build       # typecheck + production build
```

---

## The closed feedback loop

The difference between a system that *reports* and one that *learns* (FR-107–111).

```
push price -> market transacts -> read back realized sales
     ^                                      |
     |                                      v
 next run uses the           refine elasticity (capped, logged)
 refined belief   <-------   refine Monte Carlo dispersion
```

Sales history is generated up to today, so a price pushed now has no future to
be observed in. `POST /api/feedback/advance-market` asks the **Commerce
Service** to transact forward at current price-book prices using the same
generative model that produced the seed history. The platform then reads back
ordinary sales rows — it never sees the ground-truth elasticity that generated
them.

Two properties keep the loop honest rather than merely active:

- **Every adjustment is capped and logged** (FR-111). A single observation moves
  an estimate by at most 25% of its own interval half-width, and cumulative
  drift from the original regression is capped at 0.75. An unbounded update rule
  fed by its own downstream consequences diverges cheerfully; a loop that drifts
  is worse than none, because it looks like progress.
- **Promotion days are excluded from the before-window.** A promotion cuts price
  *and* buys display space, so a promo-contaminated baseline attributes the lost
  display lift to our own price move. Measured on this dataset, that confound
  put mean absolute forecast error at 28.9%; controlling for it brings it to
  12.6% — the same confound `analytics/elasticity.py` controls for with a promo
  dummy.

---

## Status against PRD v1.1

All milestones M1–M7 are implemented. Verified by execution:

| Milestone | State |
|---|---|
| M1 Foundation | Both services, synthetic data, pre-flight, gateway probe |
| M2 Analysis core | Elasticity, Monte Carlo, optimizer, baseline pricer |
| M3 Agent orchestration | Five stages, concurrent ingestion, grounding wrapper, RAG |
| M4 Compliance & autonomy | Rule veto, oscillation, bands, mode switch, kill switch |
| M5 Integration & feedback | Idempotent push, outcome readback, distribution refinement, simulation |
| M6 UI | Header monitor, settings drawer, RAG panel, theme, dashboards, mode and loop controls |
| M7 Loop & hardening | Continuous loop, caching, retries, telemetry, tests, A2A, LangGraph |

Known limits, stated plainly:

| Item | Detail |
|---|---|
| **Semantic retrieval** | Embeddings resolve `MODEL_EMBEDDING` through the gateway (preferred), then local MiniLM, then a lexical hashed-n-gram floor. `GET /api/rag/stats` reports which is live and flags `semantic: false` on the fallback. See DEVIATIONS D-10, D-13. |
| **Changing `MODEL_EMBEDDING`** | Invalidates existing collections: a Chroma collection is fixed to the width of its first vectors. The mismatch is detected and reported rather than left to fail at query time; `DELETE /api/rag/collections` re-embeds. See DEVIATIONS D-11. |
| **Grounded rationale** | Requires a reachable gateway. Without one, recommendations carry the deterministic rationale and no citations — prices are identical either way. |
| **Band thresholds** | Calibrated, not tuned per category. Snacks escalates more than Beverages because its simulated variance genuinely is higher; that is the model working, but the volatility limit is a judgement call (PRD §11). |
| **A2A** | Agent card plus `message/send` and `tasks/get`. Streaming, push notifications and cancellation are deliberately out of scope. |

---

## Configuration worth knowing about

Everything below is in `.env` — see [.env.example](.env.example) for the full set
with commentary.

| Setting | Why you would change it |
|---|---|
| `MODEL_EMBEDDING` | Gateway embedding alias. Changing it invalidates existing collections — `DELETE /api/rag/collections` to re-embed. |
| `CHUNK_STRATEGY` | `recursive` (default), `character`, `token`, `markdown`. A chunk is the unit of retrieval *and* of citation. |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | Too large and the model gets three unrelated policies; too small and a rule is severed from the condition it applies under. |
| `CHUNK_SEPARATORS` | Split points in priority order, pipe-delimited. The trailing empty entry is the character-level last resort. |
| `COMPETITOR_BIAS` | The competitor roster *and* its positioning, as JSON. A different market has different rivals. |
| `USE_OS_TRUST_STORE` | On by default. See below. |

Uploads accept `.pdf`, `.docx`, `.csv`, `.md`, `.txt`, `.json`, `.log`. PDFs are
read page by page, so a citation reads `pricing_policy:policy.pdf p.4` — a claim
a reviewer can actually go and check.

---

## Environment notes

- **Corporate TLS.** `USE_OS_TRUST_STORE=true` (the default) verifies against the
  operating system certificate store, where an intercepting proxy's root CA is
  already installed — it is how every browser on the machine reaches the same
  hosts. Python does not consult it by default: it ships `certifi`, a fixed
  bundle of public roots that cannot know about a private CA. This is what makes
  the gateway, embeddings and tiktoken work here **with verification fully on**.
  `CA_BUNDLE_PATH` overrides it. `ALLOW_INSECURE_TLS=true` remains a last resort
  that disables MITM protection entirely; it should now almost never be needed,
  is centralised in `pricing/core/tls.py`, and logs loudly at boot.
- **Model IDs are never hardcoded.** Roles map to aliases from `.env`, probed
  against the gateway at boot. If an alias does not exist, startup says so and
  lists what is actually available.
- **Zero-admin.** No Docker, no root, no system services. The continuous loop is
  a daemon thread inside the app process — it never auto-starts and dies with
  the process.
