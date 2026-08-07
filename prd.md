# Product Requirements Document

## AI-Driven Dynamic Pricing Engine for Retail

| Field | Value |
|---|---|
| **Document status** | Draft — awaiting approval |
| **Version** | 1.1 |
| **Date** | 2026-08-07 |
| **Phase** | Phase 2, Deliverable 1 of 11 |
| **Source documents** | [hackathon-problem0708.md](hackathon-problem0708.md), [AAgentic-development-master-prompt.md](AAgentic-development-master-prompt.md), [Dynamic_Pricing_Architecture.md](Dynamic_Pricing_Architecture.md) |
| **Architecture baseline** | Phase 1 decision log D1–D15 (approved 2026-08-07). **D1 superseded** — see §4.2. |

### Changelog

**v1.1** — Reconciled with `Dynamic_Pricing_Architecture.md`. Four ideas adopted from that
document: tiered autonomy (§3 W2), Monte Carlo uncertainty modelling (§5.2), oscillation and
convergence stability (§5.4), and the closed feedback loop as a learning mechanism (§5.7).
Agent topology reduced from seven to five (§4.2), superseding decision D1. Optional
continuous loop mode added (§3 W9, §5.10). Two anti-goals amended (§8).

> **Requirement numbering.** FR-001–FR-076 and NFR-001–NFR-034 retain their v1.0 numbers so
> existing references stay valid. Requirements added in v1.1 begin at FR-077 and NFR-035.

> **Authority of this document.** Once approved, this PRD is the strict source of truth.
> All subsequent deliverables — file layout, API design, agent definitions, schemas, UI
> components — must align with the requirements established here. Where this PRD and a
> later design disagree, this PRD wins unless it is formally amended.

---

## 1. Product Vision & Problem Statement

### 1.1 The problem

Retailers price with static, rule-based logic — cost-plus markups, periodic manual
reviews, blanket category discounts. That logic cannot see a competitor's midnight price
cut, a demand spike from a weather event, or 90 days of stock cover quietly eroding margin
on a slow-moving SKU. The data needed to react exists, but it is scattered across
transaction systems, competitor feeds, inventory tables, and market-trend sources, and
integrating it is slow enough that the market has moved before the analysis lands.

The cost is measured in two directions at once: revenue left on the table when demand
would have borne a higher price, and margin eroded when prices sat above what the market
would clear.

### 1.2 The product

A web-based dynamic pricing platform where a team of specialized AI agents ingests
multi-source retail data, computes price recommendations through deterministic
optimization, validates them against compliance rules, explains its reasoning with cited
evidence, and pushes human-approved prices to the commerce system of record.

### 1.3 Design philosophy: AI proposes, deterministic rules dispose

This is the single most important principle in the document, and it shapes every
requirement that follows.

Price optimization is fundamentally **mathematics** — elasticity estimation and
constrained optimization. Routing arithmetic through a language model makes it slower,
costlier, and non-reproducible. Meanwhile, compliance enforcement is a **legal**
obligation; an LLM that is right 97% of the time is not an acceptable control.

Therefore:

| Layer | Owner | Rationale |
|---|---|---|
| Numeric optimization | Deterministic Python | Reproducible, auditable, fast, free |
| Compliance enforcement | Deterministic rule engine with veto power | Legal control cannot be probabilistic |
| Uncertainty quantification | Deterministic Monte Carlo | Ranges must be computed, not asserted |
| Judgment, synthesis, interpretation | LLM agents | Where language genuinely adds value |
| Explanation and narration | LLM agents | Turning numbers into decisions humans can act on |
| Approval | Banded — automatic or human, by risk | Attention spent where it changes the outcome |

The AI contribution is **not** computing the price. It is integrating heterogeneous
signals, weighing conflicting evidence, surfacing what a human would have missed, and
explaining a recommendation well enough that a pricing manager can defend it. That is the
capability a conventional rules engine cannot supply, and Section 9 defines how we measure
it.

### 1.4 The autonomy principle: enforcement is absolute, approval is graduated

The problem statement asks us to *balance* AI autonomy with clear human oversight. A system
that demands human sign-off on every one of 500 SKUs is not balanced — it is a rubber-stamp
queue that trains its operator to click through without reading, which is worse than no
review at all.

The resolution is to separate two things that are easily conflated:

- **Enforcement** — whether compliance rules are applied — is **never** relaxed. The rule
  engine holds its veto in every operating mode, including fully autonomous. No
  configuration, mode, or override permits a rule-violating price to reach the commerce
  system.
- **Approval** — who signs off on a compliant recommendation — is **graduated by risk**.
  Confident, low-impact, stable, compliant recommendations may proceed automatically.
  Everything uncertain, high-impact, unstable, or novel goes to a human.

This is what makes human attention scarce and therefore valuable: the reviewer sees the 40
recommendations that genuinely need judgment rather than the 500 that do not. Section 3 W2
defines the bands; §5.4 defines how they are assigned.

### 1.5 Business goals

- Move pricing from static/rule-based to data-driven and scenario-tested.
- Improve revenue and margin against a measurable rule-based baseline.
- Reduce manual pricing effort by routing human attention to the decisions that need it,
  rather than by removing humans from the loop.
- Keep pricing stable and convergent — no oscillation, no erratic customer-visible swings.
- Make every recommendation explainable, cited, and auditable, whether a human approved it
  or the system did.

---

## 2. Target Audience & Personas

| ID | Persona | Role | Primary need | Success looks like |
|---|---|---|---|---|
| **P1** | **Priya — Pricing Manager** (primary user) | Owns revenue and margin targets for a category portfolio. Commercially sharp, not technical. | To set the autonomy policy and then review only the exceptions it surfaces — trusting that what passed automatically was genuinely safe. | Reviews the handful of decisions that need judgment rather than rubber-stamping hundreds, and can defend any of them upward. |
| **P2** | **Marco — Category Merchandiser** | Owns one category end to end. Deep product knowledge. | Competitive positioning and price-ladder coherence — a 500ml bottle must not cost more than the 750ml. | Catches ladder breaks and competitive gaps before customers do. |
| **P3** | **Aisha — Pricing Analyst** | Builds the analysis behind pricing strategy. Comfortable with elasticity and statistics. | Scenario simulation, visible assumptions, honest confidence intervals. | Can stress-test a strategy before it reaches Priya's queue. |
| **P4** | **Ravi — Compliance Officer** | Accountable for pricing legality and policy adherence. | Immutable audit trail: what was recommended, why, who approved, what rules were checked. | Can reconstruct any price change months later without engineering help. |
| **P5** | **Sam — Platform Engineer** (secondary) | Operates the system. | Observability, cost control, failure diagnosis. | Sees token spend, latency, cache efficiency, and agent traces without leaving the app. |

**Non-users (explicitly):** end consumers never interact with this system, and no
consumer-level data enters it. See FR-072 and Section 8.

---

## 3. User Stories & Core Workflows

### W1 — Generate a pricing run (P1, P3)

**US-001** As a Pricing Manager, I trigger a pricing run for a category so I receive
optimized price recommendations for every SKU in it.

1. User selects scope (category, SKU set, or full catalog) and an objective —
   revenue-weighted, margin-weighted, or balanced — plus optional constraints.
2. **Data & Context Agent** pulls catalog, sales history, inventory, and current prices from
   the Commerce Service over HTTP, competitor prices from the feed adapter, and market
   context from the vector store — all **concurrently**. It then profiles the data and emits
   a quality verdict. **A failing verdict halts the run** with a specific reason (FR-011).
3. **Quantitative Agent** estimates price elasticity per SKU from sales history, then runs
   Monte Carlo simulation over that elasticity and the other uncertain inputs, producing a
   revenue and margin *distribution* for each candidate price point (§5.2).
4. **Strategy & Reasoning Agent** calls the deterministic constrained optimizer against
   those distributions, then composes a recommendation per SKU with rationale, confidence,
   and citations.
5. **Validation & Compliance Agent** evaluates every recommendation against the rule engine,
   checks stability and convergence, and assigns each one an autonomy band (W2).
6. **Execution Agent** pushes auto-approved recommendations immediately; the rest land in the
   review queue.
7. Run completes. The UI streams node-by-node progress throughout.

**Acceptance:** every recommendation carries a price, a delta vs. current, an expected
revenue/margin impact **with a confidence interval**, a confidence score, a rationale, at
least one citation, a compliance verdict, and an autonomy band with the reason it was
assigned.

### W2 — Banded autonomy: review the exceptions (P1, P2)

**US-002** As a Pricing Manager, I set an autonomy policy and review only the
recommendations that fall outside it, so my attention goes where it changes the outcome.

**US-003** As a Merchandiser, I see competitive position and price-ladder impact per SKU so
I can catch structural problems.

**US-010** As a Pricing Manager, I can switch the system's operating mode or halt it
entirely at any moment, so I am never committed to automation I have stopped trusting.

#### The three bands

Every recommendation is assigned exactly one band by the Validation & Compliance Agent
(§5.4). The band and the reason for it are always visible and always audited.

| Band | Assigned when | Behaviour |
|---|---|---|
| **Auto-approve** | Confidence above threshold, price delta within limit, clean compliance verdict, Monte Carlo variance within limit, no oscillation flag, sufficient elasticity sample | Pushed without human action. Fully logged and reversible. |
| **Review** | Any auto-approve condition not met, but no escalation trigger fired | Queued for human decision. The specific failed condition is shown. |
| **Escalate** | Compliance violation, price within the margin-proximity buffer, variance beyond the volatility limit, oscillation detected, or market conditions outside historical norms | Blocked. Requires explicit human override with a written reason, and compliance violations cannot be overridden at all (FR-033). |

The three escalation triggers named in `Dynamic_Pricing_Architecture.md` — high volatility,
margin proximity, and unprecedented scenarios — are preserved exactly as the Escalate
conditions.

#### Operating modes

A global mode switch scales how much the band model is trusted:

| Mode | Auto-approve band | Review band | Escalate band |
|---|---|---|---|
| **Supervised** (default) | Sent to review — nothing pushes automatically | Review | Escalate |
| **Assisted** | Pushes automatically | Review | Escalate |
| **Autonomous** | Pushes automatically | Pushes automatically after a configurable hold window, unless a human intervenes first | Escalate |

**Supervised is the default and the safe demo posture.** Escalate always requires a human,
in every mode. The compliance veto is never relaxed in any mode.

A **kill switch** is available globally: it halts the loop, stops all pending automatic
pushes, and reverts the mode to Supervised in one action.

#### Review mechanics

- Queue sortable and filterable by band, impact, confidence, delta magnitude, compliance
  status, and category.
- Bulk-approve is permitted only within the Review band for items meeting the confidence
  threshold with a clean compliance verdict (FR-024).
- Override lets the user set a manual price with a mandatory reason; the override is
  re-validated by the compliance engine before acceptance (FR-025).
- Every action — human or automatic — is written to the audit log with actor (naming the
  system as actor for automatic approvals), band, reason, timestamp, and mode in force.

### W3 — Scenario simulation (P3, P1)

**US-004** As an Analyst, I simulate candidate prices before committing so I understand the
expected outcome and its uncertainty.

- User adjusts price for one or many SKUs and runs a what-if.
- The Monte Carlo engine projects demand, revenue, margin, and stock cover across the
  horizon, sampling over the estimated elasticity interval and the other uncertain inputs.
- Results are a **distribution, not a point estimate** — median with confidence bands, and
  the probability of falling below the margin floor stated explicitly.
- Stress-test mode runs the same scenario under extreme conditions (demand collapse,
  competitor undercut, cost spike) so the downside is visible before committing.
- Every assumption behind the simulation is listed alongside the result.
- Scenarios are savable and comparable side by side, including against the AI recommendation
  and the rule-based baseline.

### W4 — Ground the system in company knowledge (P1, P3, P4)

**US-005** As a user, I upload pricing policy and market documents so recommendations
reflect our actual rules and context, not generic assumptions.

- Upload via the RAG Grounding Panel; documents are chunked, embedded, and stored in
  ChromaDB with visible per-collection stats.
- Uploaded content becomes mandatory grounding context for subsequent reasoning calls
  (FR-040) and is citable in recommendation rationale.

### W5 — Push approved prices (P1)

**US-006** As a Pricing Manager, I push approved prices to the commerce system and see
exactly what applied and what did not.

- Applies identically to human-approved and auto-approved prices — the push path does not
  vary by who approved (NFR-040).
- Batch push carries an idempotency key; re-pushing the same batch is a no-op (FR-052).
- The Commerce Service **independently re-validates** and may reject items. Partial success
  is a first-class outcome, displayed per SKU with the rejection reason (FR-053).
- Any pushed change is reversible to its prior price in one action (FR-098).

### W6 — Measure impact and learn from it (P1, P3)

**US-007** As a Pricing Manager, I see realized impact after prices go live so I know
whether the system is working.

**US-011** As an Analyst, I see the system's forecasts get more accurate over successive
runs, so I can tell it is learning rather than merely repeating itself.

- Dashboard compares realized revenue/margin against forecast and against the rule-based
  baseline for the same SKUs.
- Realized outcomes are fed back into the elasticity estimates and the Monte Carlo input
  distributions, tightening them over successive runs (§5.7).
- Convergence is tracked explicitly: the delta between expected and actual uplift should
  shrink across iterations. A widening delta is surfaced as a warning, not buried.
- Forecast accuracy history is visible and feeds the confidence scores shown on future
  recommendations.

### W7 — Audit (P4)

**US-008** As a Compliance Officer, I reconstruct the full history of any price change.

- Per-SKU timeline: inputs, agent outputs, rules evaluated with pass/fail, approver,
  reason, push result. Exportable.

### W8 — Operate and configure (P5, P1)

**US-009** As an operator, I configure the gateway and observe system health without
touching config files or restarting.

- Settings drawer for gateway URL/port/key, SSL verification toggle, cache statistics.
- Header monitor for live token spend, active calls, and estimated cost.

### W9 — Continuous loop mode (P1, P5)

**US-012** As a Pricing Manager, I can start a continuous pricing loop that re-evaluates on
an interval, and stop it at any time, so the system responds to market movement without me
triggering every run.

1. User starts the loop from the UI, choosing an interval and a scope. **The loop never
   starts on its own** — it is off at boot and requires an explicit action (NFR-035).
2. Each iteration runs the full W1 pipeline and applies the W2 autonomy bands in force.
3. A live view shows iteration count, time to next run, what each iteration decided, and
   how many items auto-approved versus queued.
4. Any iteration that fails stops the loop and raises a visible error. The loop must never
   silently retry into a broken state (NFR-036).
5. The user stops the loop, or hits the kill switch, which stops it and reverts to
   Supervised mode.

**Why this satisfies the zero-daemon constraint.** The loop is an interval task inside the
application's own lifespan — it starts and dies with the user-space process and installs
nothing on the system. This is explicitly not a background daemon (NFR-001, NFR-035).

---

## 4. AI & System Architecture

*(Implements Phase 1 decisions D1–D15. Architectural intent only — file layout and code
structure are Deliverables 2–11.)*

### 4.1 Two-service topology (D13)

| Service | Port | Responsibility | Storage |
|---|---|---|---|
| **Commerce Service** | 8001 | Enterprise system of record. Owns catalog, sales transactions, inventory, live price book. Represents the retailer's existing e-commerce backend. | `commerce.db` |
| **Pricing AI Platform** | 8000 | The AI solution layer. Agents, RAG, optimization, compliance, A2A, telemetry. **Owns no business data.** | `app.db`, `llm_cache.db`, `./data/chroma` |
| **Web UI** | 5173 | React/Vite client. Communicates only with port 8000. | — |

The platform reaches business data **exclusively over HTTP**. There is no shared database
and no direct import across the boundary. This makes the separation demanded by the problem
statement structurally enforced rather than merely asserted — a shortcut would be visible
as an illegal import.

### 4.2 Agent topology — five agents

> **Supersedes decision D1.** Phase 1 approved a seven-agent topology. It is replaced by the
> five-agent structure from `Dynamic_Pricing_Architecture.md`, authorized 2026-08-07.
> D2–D15 are unaffected.

| # | Agent | Owns | LLM responsibility | Deterministic core |
|---|---|---|---|---|
| 1 | **Data & Context** | All ingestion, quality gating, and market context | Triages anomalies, narrates severity, synthesizes competitive positioning with citations | Freshness/completeness/outlier/schema checks, price-gap computation, cover-days and velocity, RAG retrieval |
| 2 | **Quantitative** | Demand modelling and uncertainty | Interprets the curve, assigns confidence and caveats, flags volatile scenarios | **Elasticity regression → Monte Carlo → per-price distributions** (§4.7) |
| 3 | **Strategy & Reasoning** | The pricing decision | Final recommendation, rationale, assumptions, confidence | Calls the constrained optimizer against the distributions |
| 4 | **Validation & Compliance** | Policy KB, safety, stability | **Explains violations only** | Rule engine (**holds veto**), oscillation detection, convergence tracking, anomaly detection, band assignment |
| 5 | **Execution** | Commerce integration and outcome capture | **None — no LLM in the push path** | Idempotent batch push, partial-failure handling, audit write, outcome readback |

**Orchestration (D2):** a LangGraph state machine with deterministic edges.

```
Data & Context ──▶ Quantitative ──▶ Strategy & Reasoning ──▶ Validation & Compliance
   (concurrent                                                          │
    ingestion)                                    ┌───────────┬─────────┴─────────┐
       ▲                                          ▼           ▼                   ▼
       │                                   Auto-approve   Review queue        Escalate
       │                                          │           │                   │
       │                                          └─────┬─────┘              (human override,
       │                                                ▼                     compliance never)
       │                                          Execution
       │                                                │
       └────────────── outcome feedback ────────────────┘
```

Run state is checkpointed to SQLite, making runs resumable and inspectable. The human
approval gate is a true graph interrupt, not a UI convention.

#### Two consequences of five agents, and how each is handled

**Parallelism is preserved inside Agent 1.** The seven-agent design ran three analyses
concurrently. Here, the sales pull, competitor fetch, inventory computation, and RAG
retrieval run as **concurrent tool calls within the Data & Context node**. Most of the
wall-clock benefit survives without additional agents.

**RAG is not lost — it is promoted.** There is no dedicated Competitive Intelligence agent,
but under D6 the `GroundedLLM` wrapper is the only LLM entry point in the codebase, so
retrieval-grounded context reaches **every** agent automatically. RAG becomes a cross-cutting
capability rather than one agent's private function. Competitive synthesis and citation are
Agent 1's responsibility; FR-038–044 are unaffected.

**On the Execution Agent.** It carries the "agent" label for consistency with the source
architecture document, but it is **deterministic** — formatting a payload, calling an API,
and writing an audit record involve no judgment. No LLM participates in the push path. The
label is a naming convention, not a claim of AI capability.

**Trade-off, stated plainly.** A single linear pipeline would be cheaper and faster.
Five agents are justified on four grounds: the stages own genuinely different failure modes;
the compliance veto must be an independent actor for auditability; the quantitative layer
must be isolated so its determinism is verifiable; and A2A exposure requires addressable
boundaries. The cost is latency, token spend, and harder debugging — mitigated by
deterministic edges, concurrent tool execution, and aggressive caching.

### 4.3 Model mapping and structured output (D3, D4)

All LLM traffic routes through LangChain `init_chat_model` against the LiteLLM gateway
(OpenAI-compatible). The UI never contacts the gateway.

| Role | Model tier | Used by |
|---|---|---|
| `router` | Cheapest flash-lite | Conversational routing, classification |
| `narrator` | Cheapest flash-lite | Agent 1 — anomaly narration and quality commentary |
| `analyst` | Flash | Agent 1 — competitive synthesis; Agent 2 — elasticity and variance interpretation |
| `strategist` | Pro | Agent 3 — recommendation and rationale; Agent 4 — violation explanation |
| `embeddings` | Local `all-MiniLM-L6-v2` (D5) | RAG ingestion and query |

Agent 5 (Execution) appears in no row — it makes no LLM calls.

**Model IDs are never hardcoded** (D4). Roles map to aliases resolved from environment
config and validated against the gateway at boot; startup fails loudly listing the models
actually available rather than degrading silently.

**Structured output.** Every application-logic call uses `.with_structured_output(Model,
method="json_schema")` with a single repair-retry that feeds validation errors back to the
model, then a typed error state. No raw `json.loads` on model output anywhere in the
codebase.

### 4.4 RAG and mandatory grounding (D6)

ChromaDB `PersistentClient` on local disk — an in-process library, not a server.

| Collection | Contents |
|---|---|
| `pricing_policy` | MAP agreements, pricing policy, brand guidelines |
| `market_intel` | Market trend reports, competitor intelligence |
| `product_kb` | SKU descriptions and attributes |
| `user_uploads` | Documents added at runtime via the UI |

Grounding is enforced **structurally**: a single `GroundedLLM` wrapper is the only LLM
entry point in the codebase. It injects retrieved context with source identifiers, and
recommendation-class schemas require a non-empty `citations` field validated after
generation.

**RAG is cross-cutting, not agent-specific.** Because the wrapper is the sole entry point,
every LLM-bearing agent receives retrieval-grounded context automatically — there is no
dedicated retrieval agent and none is needed. This is why collapsing the former Competitive
Intelligence agent into Agent 1 (§4.2) costs no grounding capability: retrieval was never
that agent's private function.

**Trade-off (Phase 1 conflict C5).** Injecting grounding into literally every call inflates
tokens and shifts cache keys. Retrieval breadth is therefore tuned per role rather than
blanket-injected at full width. This is a deliberate, documented softening of the "every
call" requirement in favor of the token-efficiency goal stated in the same source document.

### 4.5 Observability (D12)

Langfuse via the LangChain callback handler, capturing agent steps, duration, token usage,
tool calls, and cache outcomes. An **always-on local `structlog` JSONL sink** runs in
parallel so traces survive blocked network egress. Telemetry is never on the critical path:
a telemetry failure degrades silently and never fails a pricing run.

### 4.6 Agent interoperability (D11)

A2A exposed as `/.well-known/agent-card.json` plus JSON-RPC `message/send` and `tasks/get`.
Spec-shaped but deliberately not the full task lifecycle.

### 4.7 The modelling stack: causal layer, then uncertainty layer

This section exists because the two source designs were each incomplete here, in opposite
directions, and the resulting requirement is easy to get subtly wrong.

**The trap.** Monte Carlo simulation is only as meaningful as the distributions it samples.
It is tempting to derive those distributions from observed sales variance — but historical
variance describes how demand *has fluctuated*, not how demand *responds to a price we have
not yet set*. Simulating revenue at a candidate price requires `E[demand | price = P]`, which
is a **causal** quantity. Sampling historical variance instead yields a tight, well-formed,
confidently-presented distribution of the wrong thing. The output looks more rigorous than a
point estimate while being no more correct.

**The required stack.** Three distinct layers, in order, each with a different job:

```
   Sales history
        │
        ▼
┌───────────────────────────────────────────────┐
│  1. CAUSAL LAYER — elasticity regression      │
│     Estimates price → demand response per SKU │
│     Output: elasticity coefficient + CI       │
└───────────────────────┬───────────────────────┘
                        ▼
┌───────────────────────────────────────────────┐
│  2. UNCERTAINTY LAYER — Monte Carlo           │
│     Samples over: elasticity CI, competitor   │
│     response, cost drift, seasonality         │
│     Output: revenue/margin DISTRIBUTION per   │
│     candidate price                           │
└───────────────────────┬───────────────────────┘
                        ▼
┌───────────────────────────────────────────────┐
│  3. DECISION LAYER — constrained optimizer    │
│     Selects price maximizing the objective    │
│     subject to margin floor, bounds, damping  │
│     Output: recommended price + expected      │
│     impact with confidence interval           │
└───────────────────────────────────────────────┘
```

Layer 1 supplies the causal relationship. Layer 2 quantifies how confident we are in it.
Layer 3 decides. **No layer may be skipped.** In particular, a Monte Carlo implementation
that does not consume an upstream elasticity estimate does not satisfy §5.2 regardless of how
sophisticated its sampling is.

**What each layer contributes to the product.** Layer 1 makes the recommendation *correct*.
Layer 2 makes it *honest* — and supplies the variance number that drives the Escalate band
(§3 W2) and the volatility trigger. Layer 3 makes it *actionable*. The autonomy model depends
on Layer 2 being real: without genuine uncertainty quantification there is no principled basis
for deciding what is safe to auto-approve.

---

## 5. Functional Requirements

Priority: **M** = must-have for demo, **S** = should-have, **C** = could-have.

### 5.1 Data ingestion and quality

| ID | Requirement | Pri |
|---|---|---|
| FR-001 | Generate a synthetic retail dataset: catalog, sales transaction history, inventory, competitor prices, with realistic seasonality, promotions, and category structure. | M |
| FR-002 | Synthetic sales history must embed **known ground-truth elasticity per SKU**, enabling objective accuracy measurement (Section 9). | M |
| FR-003 | Ingest catalog, sales, inventory, and current prices from the Commerce Service over HTTP. | M |
| FR-004 | Ingest competitor prices through a pluggable `CompetitorFeed` interface; synthetic implementation is the demo path (D14). | M |
| FR-005 | Live competitor scraping available as an optional adapter behind the same interface, disabled by default. | C |
| FR-006 | Preprocessing: normalization, outlier detection, missing-value handling, feature engineering. | M |
| FR-007 | Data quality checks: freshness, completeness, schema conformance, referential integrity, statistical outliers. | M |
| FR-008 | Each check emits a structured verdict with severity and affected record counts. | M |
| FR-009 | Present a data quality report in the UI before run results. | M |
| FR-010 | Data & Context Agent narrates anomalies in business language. | S |
| FR-011 | **A failing quality gate halts the run** with a specific, actionable reason. The system must never price on data it has judged unfit. | M |
| FR-077 | Data & Context Agent executes sales retrieval, competitor fetch, inventory computation, and RAG retrieval as **concurrent** operations within a single node. | M |
| FR-078 | Data & Context Agent produces the grounded competitive-positioning synthesis with citations (absorbing the function of the former Competitive Intelligence agent). | M |
| FR-079 | A failure in any one concurrent ingestion path degrades gracefully: the run continues with reduced inputs and the affected analyses are marked lower-confidence, unless the quality gate fails outright (FR-011). | S |

### 5.2 Analysis, uncertainty, and optimization

> Implements the three-layer stack defined in §4.7. The ordering is a requirement, not an
> implementation preference: FR-080 depends on FR-012, and FR-017 depends on FR-080.

**Layer 1 — causal: elasticity estimation**

| ID | Requirement | Pri |
|---|---|---|
| FR-012 | Estimate price elasticity per SKU from sales history using deterministic regression. | M |
| FR-013 | Report elasticity as a **confidence interval, not a point estimate**, with the underlying sample size. | M |
| FR-014 | Flag SKUs with insufficient history and exclude them from confident recommendation. | M |

**Layer 2 — uncertainty: Monte Carlo simulation**

| ID | Requirement | Pri |
|---|---|---|
| FR-080 | Monte Carlo simulation produces a revenue and margin **distribution** for each candidate price, sampling over the elasticity confidence interval from FR-012/FR-013. | M |
| FR-081 | **The simulation must consume the upstream elasticity estimate.** An implementation that samples only historical sales variance does not satisfy this requirement — see §4.7. | M |
| FR-082 | Sample over at least: elasticity uncertainty, competitor price response, cost drift, and seasonal variation. | M |
| FR-083 | Iteration count is configurable, with a default balancing accuracy against the runtime budget in NFR-006. | M |
| FR-084 | Emit per-candidate-price variance and confidence bands, consumed by the autonomy band assignment (FR-089). | M |
| FR-085 | Report the **probability of falling below the margin floor** for each candidate price. | M |
| FR-086 | Stress-test mode simulates extreme conditions — demand collapse, aggressive competitor undercut, cost spike — and reports downside exposure. | S |
| FR-087 | Simulation is reproducible: a fixed seed yields identical results, so runs are auditable. | M |

**Layer 3 — decision: constrained optimization**

| ID | Requirement | Pri |
|---|---|---|
| FR-015 | Compute competitive position: price gap vs. each tracked competitor and vs. market index. | M |
| FR-016 | Compute inventory pressure: cover days, sell-through velocity, overstock/stockout flags. | M |
| FR-017 | Deterministic constrained optimizer maximizing the selected objective **over the Monte Carlo distributions**, subject to margin floor, price bounds, and elasticity. | M |
| FR-018 | Support objectives: revenue-weighted, margin-weighted, balanced. | M |
| FR-019 | Optimizer accepts run-scoped constraints (max change %, category floors/ceilings). | M |
| FR-020 | Produce forecast revenue and margin impact per SKU and aggregated per run, **each with a confidence interval**. | M |
| FR-021 | Every recommendation carries a confidence score derived from elasticity confidence, simulation variance, data quality, and competitive data freshness. | M |
| FR-088 | Optimizer respects the damping constraint from FR-091 to prevent oscillation. | M |

### 5.3 Recommendation, explanation, review

| ID | Requirement | Pri |
|---|---|---|
| FR-022 | Each recommendation states: recommended price, current price, delta (absolute and %), forecast impact **with confidence interval**, confidence score, rationale, citations, compliance verdict, and **autonomy band**. | M |
| FR-023 | Rationale must be grounded and cite retrieved sources; ungrounded recommendation text is rejected at validation (D6). | M |
| FR-024 | Review queue supports approve, reject, and override per item; bulk-approve permitted **only** within the Review band, above a confidence threshold, with clean compliance status. | M |
| FR-025 | Override requires a mandatory reason and is **re-validated by the compliance engine** before acceptance. | M |
| FR-026 | Queue sortable and filterable by **band**, impact, confidence, delta, compliance status, category. | M |
| FR-027 | Surface the price-ladder view within a product family, highlighting breaks. | S |
| FR-028 | Recommendation detail view exposes every input the decision used. | S |
| FR-029 | Conversational interface for querying recommendations in natural language. | C |
| FR-096 | Each recommendation displays **the specific reason** for its band assignment — which condition failed, with the threshold and the actual value. | M |
| FR-097 | Auto-approved recommendations remain fully visible and auditable after the fact; automatic approval never means invisible. | M |
| FR-098 | Auto-approved pushes are reversible: the user can revert a SKU to its prior price in one action. | M |

### 5.4 Compliance, stability, and autonomy

All three concerns live in Agent 4 because all three are deterministic gates on the same
decision. None of them may be implemented with an LLM in the enforcement path.

**Compliance rules**

| ID | Requirement | Pri |
|---|---|---|
| FR-030 | Deterministic rule engine evaluates every recommendation. **No LLM in the enforcement path** (D7). | M |
| FR-031 | Ship rules: MAP floor, margin floor, maximum change %, price-ladder consistency, category price bounds, round-number policy. | M |
| FR-032 | Rules are configurable without code changes. | S |
| FR-033 | A violation **blocks** the recommendation. It cannot be force-pushed, overridden, or auto-approved — **in any operating mode**. | M |
| FR-034 | LLM generates a human-readable explanation of each violation — explanation only, never enforcement. | M |
| FR-035 | Every rule evaluation is persisted with pass/fail and evaluated values for audit. | M |
| FR-036 | System refuses to produce individual-level or personalized prices (D8). | M |
| FR-037 | Guardrail against sensitive-data leakage into prompts, with automatic redaction. | M |

**Stability and convergence**

Price oscillation is a distinct failure mode from a single bad price, and the max-change-%
cap in FR-031 does not prevent it: a SKU can ping-pong between two price points across
consecutive runs with every individual step inside the cap. These requirements address that.

| ID | Requirement | Pri |
|---|---|---|
| FR-090 | Detect oscillation by examining each SKU's price history across runs — direction reversals within a configurable window, and repeated returns to a recent price point. | M |
| FR-091 | Apply **damping** to recommendations for SKUs showing oscillation: constrain the permitted move toward the recommended price rather than allowing the full step. | M |
| FR-092 | Track convergence per SKU and per run: the delta between forecast and realized uplift should narrow across iterations. A widening delta raises a visible warning. | M |
| FR-093 | Detect anomalous recommendations statistically — prices far outside the SKU's own historical distribution or its category peers — independent of whether any compliance rule fired. | M |
| FR-094 | Enforce a per-SKU minimum interval between price changes, configurable, to protect customer-visible price stability. | S |
| FR-095 | Persist stability signals with the recommendation so oscillation and convergence history are auditable. | M |

**Autonomy band assignment**

| ID | Requirement | Pri |
|---|---|---|
| FR-089 | Assign every recommendation exactly one band — Auto-approve, Review, or Escalate — per the conditions in §3 W2, evaluated deterministically. | M |
| FR-099 | Escalate is triggered by any of: compliance violation, price within the margin-proximity buffer, Monte Carlo variance beyond the volatility limit, oscillation flag, or market conditions outside historical norms. | M |
| FR-100 | Band thresholds (confidence, delta, variance, margin buffer) are configurable without code changes. | M |
| FR-101 | Global operating mode — Supervised, Assisted, Autonomous — governs which bands may push automatically. **Supervised is the default.** | M |
| FR-102 | A **kill switch** halts the loop, cancels pending automatic pushes, and reverts the mode to Supervised in a single action. | M |
| FR-103 | Mode changes and kill-switch activations are audited with actor, timestamp, and prior state. | M |
| FR-104 | The mode in force at decision time is recorded on every recommendation, so historical decisions remain interpretable after the mode changes. | M |

### 5.5 RAG and grounding

| ID | Requirement | Pri |
|---|---|---|
| FR-038 | Ingest documents into ChromaDB with chunking, local embedding, and metadata. | M |
| FR-039 | UI panel displays per-collection embedding statistics (document count, chunk count, last updated). | M |
| FR-040 | Uploaded documents become mandatory grounding context for subsequent reasoning calls. | M |
| FR-041 | Upload and embed documents at runtime without restart. | M |
| FR-042 | Retrieved context is attributed with source identifiers usable as citations. | M |
| FR-043 | No LLM call may bypass the `GroundedLLM` wrapper; enforced by automated check. | M |
| FR-044 | Retrieval breadth configurable per agent role. | S |

### 5.6 Scenario simulation

| ID | Requirement | Pri |
|---|---|---|
| FR-045 | What-if simulation for arbitrary candidate prices across one or many SKUs. | M |
| FR-046 | Project demand, revenue, margin, and stock cover over a configurable horizon. | M |
| FR-047 | Present results as **distributions with explicit assumptions**, never bare point estimates. | M |
| FR-048 | Save, name, and compare scenarios side by side. | S |
| FR-049 | Compare any scenario against the AI recommendation and the rule-based baseline. | M |
| FR-050 | Narrate scenario outcomes in business language. | S |
| FR-105 | Simulation reuses the **same Monte Carlo engine** as the pricing pipeline (§5.2), so a simulated outcome and a recommendation forecast are directly comparable rather than produced by different code paths. | M |
| FR-106 | Expose stress-test scenarios (FR-086) directly in the simulation UI. | S |

### 5.7 Commerce integration

| ID | Requirement | Pri |
|---|---|---|
| FR-051 | Commerce Service exposes catalog, sales history, inventory, price read, batch price write, and price history endpoints. | M |
| FR-052 | Batch price push is **idempotent** via a batch key; re-push is a no-op (D15). | M |
| FR-053 | Commerce Service **independently re-validates** incoming prices and may reject items; partial success is displayed per SKU with reasons. | M |
| FR-054 | Every push attempt and outcome is recorded in the audit log, including whether the approval was human or automatic. | M |
| FR-055 | Platform reads back realized sales post-push to close the feedback loop. | **M** |

**Closed feedback loop — learning, not just reporting**

| ID | Requirement | Pri |
|---|---|---|
| FR-107 | Realized outcomes are fed back into the elasticity estimates, refining them with each observed price change rather than re-estimating from static history alone. | M |
| FR-108 | Monte Carlo input distributions are refined from observed forecast error, so simulated intervals tighten as evidence accumulates. | M |
| FR-109 | Forecast-vs-realized error is tracked per SKU and feeds the confidence score on subsequent recommendations (FR-021). | M |
| FR-110 | Convergence is measurable across iterations and displayed (FR-092). A system that is not converging must say so. | M |
| FR-111 | Refinement is bounded and inspectable: the magnitude of any adjustment to an elasticity estimate is capped and logged, so the loop cannot silently drift into a bad state. | M |

### 5.8 Dashboards and audit

| ID | Requirement | Pri |
|---|---|---|
| FR-056 | Performance dashboard: revenue and margin impact, forecast vs. realized, recommendation acceptance rate. | M |
| FR-057 | **Baseline comparison dashboard** contrasting AI recommendations against the rule-based baseline pricer on identical data (Section 9.3). | M |
| FR-058 | Per-SKU audit timeline: inputs, agent outputs, simulation distributions, rule evaluations, band and its reason, approver (human or system), mode in force, reason, push result. | M |
| FR-059 | Audit records are append-only. | M |
| FR-060 | Export audit trail. | S |
| FR-061 | Run history with status, duration, token cost, and outcome. | M |
| FR-112 | **Stability dashboard**: oscillation incidents, damping applied, convergence trend, and forecast-error trajectory over time. | M |
| FR-113 | **Autonomy dashboard**: band distribution per run, auto-approve rate, escalation reasons ranked by frequency, and mode-change history. | M |
| FR-114 | Audit view can filter to automatic approvals alone, so a compliance reviewer can inspect exactly what the system decided without human involvement. | M |

### 5.9 Mandated UI elements

*(Explicitly required by master prompt §5. All are must-have.)*

| ID | Requirement | Pri |
|---|---|---|
| FR-062 | React + React Router + Vite + Tailwind. Minimal enterprise aesthetic. | M |
| FR-063 | **No emojis anywhere in the UI.** All icons from a vector library (Lucide). | M |
| FR-064 | Explicit light/dark theme toggle via CSS variables and Tailwind semantic tokens; preference persists. | M |
| FR-065 | **Global header LLM monitor**, persistent across all routes, showing active LLM calls, cumulative input/output tokens, and estimated cost (LiteLLM fallback rates when a model is unpriced). | M |
| FR-066 | **Settings gear drawer**, globally accessible, containing gateway URL, port, and API key inputs; an iOS-style toggle for "Disable SSL Verification"; and live cache hit/miss statistics. | M |
| FR-067 | Settings changes apply at runtime without restart. | M |
| FR-068 | **Universal RAG grounding panel** showing embedding stats with runtime document upload (FR-039, FR-041). | M |
| FR-069 | Live run progress streamed to the UI, showing current agent and completed nodes. | M |
| FR-070 | Responsive from 1280px; keyboard-navigable review queue. | S |
| FR-115 | **Operating mode control** — Supervised / Assisted / Autonomous — visible in the global header with the current mode always displayed, never hidden in a submenu. | M |
| FR-116 | **Kill switch** reachable from the global header at all times, visually distinct, with a single confirmation step. | M |
| FR-117 | **Loop control panel**: start/stop, interval and scope selection, iteration count, time to next run, and a live log of what each iteration decided. | M |
| FR-118 | Band indicators use shape or label in addition to colour, so status is not conveyed by colour alone. | M |
| FR-119 | Monte Carlo results render as distribution visualizations — confidence bands, not bare numbers — in both light and dark themes. | M |
| FR-120 | A persistent indicator shows when the loop is running, visible on every route. | M |

### 5.10 Continuous loop mode

| ID | Requirement | Pri |
|---|---|---|
| FR-121 | Interval-triggered pricing loop running inside the application lifespan, started and stopped exclusively from the UI. | M |
| FR-122 | **The loop never auto-starts.** It is off at boot and requires explicit user action every time (NFR-035). | M |
| FR-123 | Each iteration executes the full W1 pipeline and honours the autonomy bands and operating mode in force at that moment. | M |
| FR-124 | A failed iteration **stops the loop** and surfaces a visible error. No silent retry loops (NFR-036). | M |
| FR-125 | Loop state, iteration history, and per-iteration decisions are persisted and auditable. | M |
| FR-126 | The loop stops cleanly on application shutdown, completing or abandoning the in-flight iteration without corrupting run state (NFR-023). | M |
| FR-127 | Configurable maximum iterations per session, as a runaway backstop. | S |

### 5.11 Platform and interoperability

| ID | Requirement | Pri |
|---|---|---|
| FR-071 | A2A agent card and JSON-RPC `message/send` / `tasks/get` (D11). | S |
| FR-072 | Backend-only LLM routing; the client never holds gateway credentials or calls the gateway. | M |
| FR-073 | Two-tier LLM cache — exact-hash and semantic — persisted to SQLite, with hit/miss statistics exposed to the UI. | M |
| FR-074 | Cross-platform startup scripts (`startup.sh`, `startup.bat`) with pre-flight validation. | M |
| FR-075 | Pre-flight verifies venv, Node version, required environment variables, database and Chroma directory permissions, port availability for 8000/8001/5173, and gateway reachability. Fails fast with specific messages. | M |
| FR-076 | Commerce Service must be healthy before the platform accepts pricing runs. | M |

---

## 6. Non-Functional Requirements

### 6.1 Environment and execution

| ID | Requirement |
|---|---|
| NFR-001 | **User-space execution only.** Zero Docker, zero containers, zero root/admin privileges, zero system-wide daemons. |
| NFR-002 | Python 3.x in `venv`; Node.js `>=20.19` with 22.x LTS preferred (resolves source conflict C3). |
| NFR-003 | All network services on unprivileged ports (>1024): 8000, 8001, 5173. |
| NFR-004 | All storage local, file-based, embedded, in-process: ChromaDB and SQLite. No external database server. |
| NFR-005 | `app.db` and `llm_cache.db` are separate files in WAL mode to avoid write contention (D9). |

### 6.2 Performance

| ID | Requirement |
|---|---|
| NFR-006 | Pricing run over a 500-SKU category completes within **5 minutes** on a developer laptop. Revised upward from 3 minutes in v1.0 to accommodate the Monte Carlo layer. |
| NFR-007 | Data & Context ingestion paths execute **concurrently** within the node, not sequentially (FR-077). |
| NFR-008 | Cached LLM responses return in under 100ms. |
| NFR-009 | Interactive UI actions respond within 500ms; long operations stream progress. |
| NFR-010 | Scenario simulation returns within 5 seconds for a single SKU. |
| NFR-037 | Monte Carlo simulation consumes no more than **60 seconds** of the 500-SKU run budget. Iteration count (FR-083) is tuned to hold this, and the trade-off between iterations and interval width is documented. |
| NFR-038 | Monte Carlo is CPU-bound and must not block the event loop; it runs off the async path so UI progress streaming stays responsive. |

### 6.3 Security and privacy

| ID | Requirement |
|---|---|
| NFR-011 | Gateway credentials never reach the client and are never written to disk in plaintext; runtime-supplied keys stay in memory. |
| NFR-012 | Automatic secret and PII redaction in all logs and traces. |
| NFR-013 | No consumer-level or personally identifiable data enters the system (D8, FR-036). |
| NFR-014 | Proprietary sales data never leaves the local environment except as prompt content to the configured gateway. |
| NFR-015 | Append-only audit log for every recommendation, approval, override, and push. |

### 6.4 Corporate TLS handling (D10)

| ID | Requirement |
|---|---|
| NFR-016 | **Preferred path:** trust the corporate root CA via `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`. Attempted first. |
| NFR-017 | TLS verification bypass permitted **only** behind an explicit `ALLOW_INSECURE_TLS=true` flag. Never the default. |
| NFR-018 | Bypass logic centralized in a single helper — no scattered `verify=False`. |
| NFR-019 | When bypass is active, log a prominent warning at boot and display a persistent indicator in the UI. |
| NFR-020 | Node-side `NODE_TLS_REJECT_UNAUTHORIZED=0` confined to the Vite dev proxy; never in production build output. |

> **Security trade-off, stated explicitly.** Disabling TLS verification removes protection
> against man-in-the-middle attacks: the client will accept any certificate, including a
> forged one. This is tolerable **only** because traffic is confined to a corporate network
> already performing sanctioned interception, and because the alternative is a
> non-functional prototype. It is unacceptable for production or for any traffic crossing
> an untrusted network. Trusting the corporate root CA (NFR-016) achieves the same
> connectivity without surrendering authentication and is strongly preferred wherever the
> CA certificate can be obtained.

### 6.5 Reliability

| ID | Requirement |
|---|---|
| NFR-021 | Exponential-backoff retry on transient gateway failures (429, 500, 502, 503, 504). |
| NFR-022 | Boot-time environment validation via Pydantic schemas; fail fast with actionable messages. |
| NFR-023 | Graceful `SIGINT`/`SIGTERM` handling: flush telemetry, checkpoint runs, close SQLite cleanly. |
| NFR-024 | Runs are checkpointed and resumable after interruption. |
| NFR-025 | Telemetry failure never fails a pricing run. |
| NFR-026 | Commerce Service unavailability produces a clear, actionable error — never a silent partial run. |
| NFR-035 | The continuous loop is an **in-process interval task inside the application lifespan** — not a system daemon, not an OS-scheduled job, and it installs nothing. It never auto-starts, and it terminates with the process. This is how §5.10 coexists with NFR-001. |
| NFR-036 | A failed loop iteration **stops the loop** and raises a visible error. The loop must never retry silently into a broken state or continue pricing on stale inputs. |
| NFR-039 | Automatic pushes are reversible. Every auto-approved change records the prior price so a single action restores it (FR-098). |
| NFR-040 | Any recommendation the system approves automatically must satisfy every check a human-approved one does. Autonomy changes who approves, never what is verified. |

### 6.6 Observability

| ID | Requirement |
|---|---|
| NFR-027 | Structured JSON logging (`structlog`) recording latency, token usage, model, and cache hit/miss for every agent action and LLM call. |
| NFR-028 | Langfuse tracing with a local JSONL fallback sink always active (D12). |
| NFR-029 | Token spend and estimated cost attributable per run and per agent. |

### 6.7 Maintainability

| ID | Requirement |
|---|---|
| NFR-030 | **Hard limit 300–400 LOC per file.** |
| NFR-031 | Strict separation of concerns: agents and tools, API routes and A2A, RAG services, prompts and schemas, database and cache, shared utilities. |
| NFR-032 | Framework independence — LLM provider swappable via configuration, not code changes. |
| NFR-033 | Automated tests covering the golden path, compliance veto, idempotency, structured-output repair, and grounding enforcement. |
| NFR-034 | Composition over inheritance; single-responsibility modules. |

---

## 7. Assumptions

| ID | Assumption | Risk if wrong |
|---|---|---|
| A-01 | The LiteLLM gateway is reachable and exposes at least one working chat model. | Blocking — no AI functionality. Mitigated by boot probe (FR-075). |
| A-02 | Gemini model aliases in the source document may not exist as written; resolution is config-driven (D4). | Low — fails loudly at boot with the real list. |
| A-03 | The local embedding model can be downloaded once, or gateway embeddings are available as fallback (D5). | Medium — RAG unavailable if both fail. |
| A-04 | Langfuse egress may be blocked; the local sink covers this (D12). | Low. |
| A-05 | Synthetic data is acceptable for demonstration; no real retail data is available. | Low — explicitly sanctioned by the problem statement. |
| A-06 | Single-user demo context; no multi-tenant authentication or RBAC required. | Low — see Section 8. |

---

## 8. Out of Scope (Anti-Goals)

Explicitly excluded to prevent scope creep. Each is a deliberate decision, not an oversight.

| Excluded | Reasoning |
|---|---|
| **Personalized or individual-level pricing** | Legal exposure around discriminatory pricing. Segment- and SKU-level only. Not a time constraint — a permanent product boundary (D8). |
| **Unbounded autonomy** *(amended v1.1)* | Bounded autonomy is now in scope (§3 W2): the system may auto-approve compliant, confident, stable, low-impact recommendations. What remains excluded is autonomy without bounds — no mode permits bypassing the compliance veto, skipping the Escalate triggers, or pushing a price that fails any check a human-approved price must pass (NFR-040). |
| **Real e-commerce platform integration** (Shopify, SAP, Magento) | The local Commerce Service demonstrates the integration pattern without external dependency. |
| **Production-grade authentication, RBAC, multi-tenancy** | Single-user demo context. The audit log records an actor, so the seam exists for later. |
| **Live competitor web scraping as the demo path** | Fragile through a corporate proxy and a live-demo failure risk. Adapter interface exists (FR-005). |
| **Event-driven streaming price updates** *(amended v1.1)* | An interval-triggered loop is now in scope (§5.10). What remains excluded is true event-driven streaming — message brokers, change-data-capture, per-transaction reactive repricing. That infrastructure cannot be justified under the zero-daemon constraint and adds no demo value over an interval loop. |
| **LLM participation in the price push path** | The Execution Agent is deterministic (§4.2). Formatting a payload and calling an API involve no judgment, and putting a language model there would add failure modes without capability. |
| **Deep learning demand forecasting** | Regression with honest confidence intervals is more defensible and interpretable than an unexplainable neural forecast on synthetic data. |
| **Mobile-native applications** | Desktop web serves the personas, who work at desks. |
| **Multi-currency and multi-region tax** | Single currency, single region. Orthogonal complexity. |
| **Cloud deployment, CI/CD, containerization** | Directly prohibited by NFR-001. |
| **Autonomous agent-to-agent negotiation** | A2A is exposed for interoperability (FR-071), not used for emergent multi-agent bargaining. |

---

## 9. Success Metrics

### 9.1 Product effectiveness

| Metric | Definition | Target |
|---|---|---|
| **Revenue uplift** | Forecast revenue from AI recommendations vs. rule-based baseline on identical data | ≥ 5% |
| **Margin protection** | Margin under AI recommendations vs. baseline | No degradation while achieving revenue uplift |
| **Pricing accuracy** | Deviation of recommended price from the theoretical optimum computed against **ground-truth elasticity** (FR-002) | Within 10% for high-confidence SKUs |
| **Recommendation acceptance rate** | Share approved without override | ≥ 70% |
| **Manual intervention reduction** | Share of SKUs auto-approved without human action, in Assisted mode | ≥ 60% |
| **Escalation precision** | Share of escalated items a human agrees needed escalation — measures whether the bands earn their friction | ≥ 80% |
| **Forecast reliability** | Realized vs. forecast impact after push | Within stated confidence interval ≥ 80% of the time |
| **Price stability** | SKUs showing oscillation across consecutive runs | < 2% |
| **Convergence** | Forecast-vs-realized error trend across successive iterations | Narrowing, not widening |

> Ground-truth elasticity embedded in the synthetic data (FR-002) is what makes pricing
> accuracy objectively measurable rather than merely asserted. This is the strongest
> evidence available that the recommendations are genuinely good.

### 9.2 Technical

| Metric | Target |
|---|---|
| Pricing run, 500 SKUs | < 5 minutes |
| Monte Carlo share of run budget | < 60 seconds |
| Cache hit rate on repeated runs | ≥ 40% |
| Structured output validity after repair-retry | ≥ 99% |
| Grounding compliance — recommendations with valid citations | 100% |
| Compliance veto effectiveness — violating prices reaching commerce, **in any mode** | 0 |
| Auto-approved prices that would have failed a human-path check | 0 |
| Simulation reproducibility — identical results from a fixed seed | 100% |
| Loop iterations continuing after a failure | 0 |
| Idempotency — duplicate applied changes on re-push | 0 |
| Files exceeding 400 LOC | 0 |

### 9.3 Demonstrating AI value over conventional approaches

The problem statement requires showing that AI **materially improves** on a conventional
approach. Assertion is insufficient, so the system ships a **rule-based baseline pricer**
— cost-plus markup with category discount rules, representing current retail practice —
that runs on identical data and is compared side by side (FR-057).

Three claims, each with evidence:

1. **Better prices.** Measured against ground-truth optimum, not opinion (9.1).
2. **Signals the baseline cannot see.** Cases where competitive positioning, inventory
   pressure, and elasticity conflict — the baseline applies a fixed rule; the agents weigh
   the evidence and explain the resolution.
3. **Defensible decisions.** Every recommendation carries a cited, grounded rationale a
   pricing manager can act on and a compliance officer can audit. The baseline produces a
   number with no reasoning at all.
4. **It knows when it does not know.** This is the claim that makes the other three
   actionable. A rules engine outputs the same unqualified number whether it has three years
   of clean history or three noisy weeks — so it can never be trusted to act unsupervised.
   Monte Carlo over an estimated elasticity interval yields a calibrated confidence, which is
   precisely what makes bounded autonomy safe (§3 W2). Demonstrable directly: degrade a SKU's
   history, watch confidence fall and the recommendation move from Auto-approve to Escalate,
   while the baseline's output does not change at all.

---

## 10. Release Scope

| Milestone | Contents | Requirements |
|---|---|---|
| **M1 — Foundation** | Both services up, synthetic data generated, pre-flight passing, gateway verified | FR-001–005, FR-051, FR-074–076, NFR-001–005 |
| **M2 — Analysis core** | Elasticity regression, competitive and inventory analysis, **Monte Carlo layer**, deterministic optimizer, baseline pricer | FR-006–021, FR-057, FR-080–088 |
| **M3 — Agent orchestration** | Five-agent LangGraph topology, concurrent ingestion, structured output, grounding wrapper, RAG | FR-022–023, FR-038–044, FR-069, FR-077–079 |
| **M4 — Compliance, stability, autonomy** | Rule engine with veto, oscillation and convergence detection, band assignment, mode switch, kill switch, review queue, audit | FR-024–037, FR-058–061, FR-089–104, FR-112–114 |
| **M5 — Integration, feedback, simulation** | Idempotent push, partial-failure handling, outcome readback and distribution refinement, Monte Carlo simulation | FR-045–055, FR-105–111 |
| **M6 — UI completion** | Header monitor, settings drawer, RAG panel, theme, dashboards, mode and loop controls | FR-056, FR-062–070, FR-115–120 |
| **M7 — Loop and hardening** | Continuous loop mode, caching, retries, telemetry, graceful shutdown, tests, A2A | FR-071–073, FR-121–127, NFR-021–040 |

### Scope impact of v1.1 — flagged, not absorbed

Three additions are real engineering rather than documentation: the Monte Carlo layer,
oscillation and convergence detection, and distribution refinement in the feedback loop.
**M2 and M4 grow materially** against the hackathon timebox.

If time compresses, cut in this order:

1. **Distribution refinement (FR-107–108, FR-111)** — degrades gracefully to
   forecast-vs-realized reporting. A chart instead of a learning loop. Safest cut.
2. **Stress-test mode (FR-086, FR-106)** — valuable, not load-bearing.
3. **Autonomous mode (the third band setting)** — ship Supervised and Assisted only. The
   band model still demonstrates fully.

**Do not cut** the Monte Carlo layer or the band assignment: §9.3 claim 4 depends on
calibrated confidence, and without it the autonomy story has no foundation.

---

## 11. Open Items for Build-Time Verification

Carried from the Phase 1 decision log. None reopens an approved decision.

| Item | Resolution path |
|---|---|
| Which model aliases the gateway actually exposes | Boot probe (FR-075); update role mapping config |
| Whether the proxy permits the local embedding model download | Pre-flight check; fall back to gateway embeddings (A-03) |
| Whether the corporate root CA is exportable | Attempt NFR-016 before enabling NFR-017 |
| Whether Langfuse egress succeeds through the proxy | Local sink covers either outcome (NFR-028) |
| Final compliance rule set and thresholds | Configurable per FR-032; defaults per FR-031 |
| **Monte Carlo iteration count** | Calibrate against NFR-037's 60-second budget; document the accuracy/runtime trade-off |
| **Oscillation detection window and reversal threshold** | Tune against synthetic run history; too tight suppresses legitimate moves, too loose misses ping-ponging |
| **Autonomy band thresholds** (confidence, delta, variance, margin buffer) | Calibrate so the auto-approve share is meaningful but escalation precision stays above the §9.1 target |
| **Damping factor** | Balance oscillation suppression against responsiveness to genuine market movement |
| **Loop interval default** | Short enough to demo live, long enough that iterations complete without overlapping |

---

## 12. Approval

This PRD requires explicit approval before Phase 2 Deliverables 2–11 (file layout, startup
scripts, API architecture, agent definitions, schemas, caching, logging, UI architecture,
roadmap) may be produced.

| Deliverable | Status |
|---|---|
| 1. `prd.md` | **Awaiting approval** |
| 2–11. Architecture, code, and implementation | Blocked pending approval of this document |
