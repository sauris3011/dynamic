# Deviations from the PRD and Phase 1 Decision Log

Every departure from an approved decision, with the reason. Recorded here rather
than absorbed silently so they can be reviewed or reversed.

---

## D-01 — Package named `pricing/`, not `platform/`

**Decision affected:** none (naming only)

A top-level package called `platform` shadows Python's stdlib `platform` module,
which several dependencies import. The AI service package is therefore `pricing/`.
`commerce/` is unchanged.

---

## D-02 — Embeddings: ChromaDB ONNX MiniLM, not `sentence-transformers`

**Decision affected:** D5 (local embeddings via `sentence-transformers/all-MiniLM-L6-v2`)

Same model family, but obtained through ChromaDB's bundled ONNX runtime rather
than `sentence-transformers`. The latter pulls PyTorch — multiple gigabytes —
for a model that is ~80MB in ONNX form. D5's intent (local, free, no daemon) is
fully preserved; only the delivery mechanism changed.

---

## D-12 — The OS trust store is the preferred TLS path

**Decision affected:** NFR-016 (trust the corporate root CA) — **now actually achieved**

D-03 recorded that the corporate proxy blocks the MiniLM download, and NFR-016
prescribed trusting the corporate root CA via an exported bundle. Nobody had
exported one, so in practice everything fell to `certifi` and failed with
`CERTIFICATE_VERIFY_FAILED` — the LLM gateway, embeddings, tiktoken, and the
MiniLM download alike. The visible symptom was "no semantic model available",
several layers away from the cause.

The root CA is already installed in the Windows certificate store — it is how
every browser on the machine reaches the same hosts. Python does not consult it:
it ships `certifi`, a fixed bundle of public roots that cannot know about a
private CA. `truststore` bridges that gap.

`pricing/core/tls.py` now prefers the OS store (`USE_OS_TRUST_STORE`, default
on), ahead of `ALLOW_INSECURE_TLS` and behind an explicit `CA_BUNDLE_PATH`.
Verified: gateway reachable, embeddings returning 3072-dimension vectors, and
tiktoken able to fetch its encoding — all with verification fully **on**.

**Why it is installed process-wide.** Returning an `SSLContext` only covers
clients we construct ourselves. It does nothing for the libraries underneath —
`tiktoken` over `requests`, ChromaDB's ONNX downloader, `huggingface_hub` — each
of which builds its own default context. `truststore.inject_into_ssl()` patches
`ssl.SSLContext` itself, so every library benefits. That is global
monkeypatching and worth being uneasy about: it is the library's documented
purpose, it is gated behind a setting, and it only ever *adds* certificates the
machine already trusts.

`ALLOW_INSECURE_TLS` remains, and should now almost never be needed.

---

## D-13 — LangChain for embeddings, splitting and Document loading

**Decision affected:** none (implements D3's intent consistently)

Three parts of the RAG path were hand-rolled and are now LangChain:

* **Embeddings** use `langchain_openai.OpenAIEmbeddings` against the gateway,
  mirroring how chat models are built (D3). Batching, retry and ordering come
  from the client library rather than from a second implementation of the same
  logic. `check_embedding_ctx_length=False` is set deliberately: LangChain
  otherwise pre-chunks with tiktoken against a *known OpenAI* model name, and a
  gateway alias like `azure/genailab-maas-text-embedding-3-large` is not a name
  tiktoken knows. Text arrives already chunked, so that path is redundant.
* **Chunking** uses `langchain_text_splitters`, configurable via
  `CHUNK_STRATEGY`, `CHUNK_SIZE`, `CHUNK_OVERLAP` and `CHUNK_SEPARATORS`.
  Recursive is the default because it tries paragraph, then line, then
  sentence, then word before cutting mid-word — a citation severed mid-sentence
  is not evidence a human can check.
* **Loading** produces `langchain_core.documents.Document`, with PDFs read page
  by page so a citation can say `policy.pdf p.4` rather than naming the whole
  file. Verified: page numbers survive loading, chunking and storage into the
  retrieved citation id.

**`langchain-community` was rejected.** It is the obvious home for the loaders
and it is being sunset — importing it emits a deprecation warning pointing at
standalone replacements that do not all exist yet. Taking a dependency on an
unmaintained package to save roughly eighty lines is a bad trade, particularly
behind a proxy where every transitive dependency is another download that can
fail. `pypdf` and `docx2txt` are used directly and return the same type.

The `token` strategy needs a tiktoken encoding downloaded on first use; if that
fails it falls back to recursive with a logged reason rather than failing an
ingest, because a chunking strategy is not worth losing a document over.

---

## D-14 — Competitor roster and positioning are configuration

**Decision affected:** D14 (pluggable competitor feed) — completes its intent

`SyntheticCompetitorFeed.BIAS` was a hardcoded dict of three named retailers.
That quietly undercut the point of the `CompetitorFeed` protocol: the interface
was pluggable while the market it described was not.

The roster and its positioning now come from `COMPETITOR_BIAS` (JSON), with
`COMPETITOR_IDIOSYNCRATIC_PCT`, `COMPETITOR_DRIFT_PCT` and
`COMPETITOR_OUT_OF_STOCK_RATE` alongside. Values are validated at boot — a
multiplier outside 0.1–5.0 or malformed JSON fails with a specific message
rather than producing nonsense prices.

The constructor still accepts an explicit `bias`, which is what the tests use: a
test that had to mutate the environment to change a competitor's positioning
would be testing the config loader rather than the feed.

---

## D-10 — Gateway embeddings are the preferred path (`MODEL_EMBEDDING`)

**Decision affected:** D5 (local embeddings) — supersedes the ordering in D-02/D-03

D5 chose local embeddings so retrieval costs nothing at the gateway and works
with no network. That reasoning holds everywhere except the environment this
actually runs in, where the *local* path is the one that fails: the MiniLM
download is blocked by TLS interception (D-03), while the gateway is reachable
because it already holds the credentials and terminates TLS the way the proxy
expects.

`pricing/rag/gateway_embeddings.py` calls the gateway's OpenAI-compatible
`/v1/embeddings` (falling back to `/embeddings`) with the alias from
`MODEL_EMBEDDING`. Model IDs stay out of source, exactly as for the chat roles
(D4). The strategy chain is now: **gateway → local MiniLM → hashed n-grams**,
each weaker than the one above, with `GET /api/rag/stats` reporting which is live
and `semantic: false` when only the lexical floor is available.

Measured on `azure/genailab-maas-text-embedding-3-large`: 3072 dimensions, and
"how much profit must we keep on every item?" retrieves the margin policy
without sharing the words *margin* or *floor* — retrieval the hashed fallback
cannot do by construction.

Two decisions worth recording:

* **The embedding alias is deliberately not in `registry.ROLES`.** That probe
  gates `probe_gateway().usable`, which gates every narration call. Including
  embeddings would mean a gateway lacking the embedding model silently switches
  off all narration over an unrelated capability.
* **This does not route through `GroundedLLM`.** FR-043 exists so grounding
  injection and structured-output validation are unconditional on *chat* calls.
  Neither applies to an embedding, and routing it through the wrapper would
  recurse — the wrapper calls retrieval, and retrieval calls embeddings. This is
  the substrate the wrapper is built on, so it sits below it.

---

## D-11 — Embedding changes invalidate collections, detected rather than assumed

**Decision affected:** none (hazard introduced by D-10)

A Chroma collection is fixed to the width of the first vectors written into it.
Switching `MODEL_EMBEDDING` from a 384-dimension model to a 3072-dimension one
does not upgrade the collections — it breaks them, and the failure surfaces as
an opaque dimensionality error on the next query. Worse, at *equal* width the
query succeeds while retrieving against a vector space the query no longer lives
in, which presents as merely poor results.

`pricing/rag/store.py` writes an embedding signature alongside the Chroma files
and compares it on every `stats()` call. A mismatch is reported with the stored
and current functions, whether the widths are compatible, and what to do about
it; the RAG panel shows it inline.

Nothing is deleted automatically. `DELETE /api/rag/collections` drops and
re-creates them under the current function, and it is explicit because the
alternative is a config typo silently destroying an ingested corpus. Only
derived data is affected — the source documents are the operator's own files.

---

## D-03 — Hashed n-gram embedding fallback added

**Decision affected:** D5, PRD assumption A-03

**A-03 is confirmed, not hypothetical.** In this environment the MiniLM download
fails:

```
CERTIFICATE_VERIFY_FAILED / ConnectTimeout: _ssl.c:983: handshake operation timed out
```

The corporate TLS interception blocks it, and ChromaDB's downloader does not
consult `CA_BUNDLE_PATH`. Rather than ship a RAG panel that cannot ingest
anything, `pricing/rag/embeddings.py` falls back to deterministic hashed
character/word n-grams (384d, zero download, zero network).

**This is a real downgrade and the code says so.** The fallback is *lexical*, not
semantic: it matches shared vocabulary and character patterns, so a paraphrase
using entirely different words may not retrieve. `GET /api/rag/stats` reports
which function is active (`embedding_model`) so nobody mistakes one for the
other.

**To get true semantic retrieval**, in order of preference: set
`MODEL_EMBEDDING` to a gateway embedding alias (D-10) — this works even with the
download blocked — or set `CA_BUNDLE_PATH` to the corporate root CA so MiniLM
can load. Both are attempted on every boot, gateway first, and the hashed
fallback is used only when neither is available.

---

## D-04 — Price ladder replaces naive charm rounding

**Decision affected:** none (defect found during verification)

Snapping candidate prices only to `.49`/`.99` collapses the optimizer's search
space. Within a ±10% band, a £2.49 item has exactly one valid point — itself — so
the optimizer "recommended" no change on 73% of the catalog. That is a
grid-resolution bug that presents as conservatism.

`pricing/analytics/price_points.py` implements a magnitude-scaled retail ladder
(10p steps under £5, 50p to £20, £1 to £50, £2 above). Holds dropped from 73% to
46%, and mean recommended change rose from 1.97% to 3.91%.

All arithmetic there is in **integer cents**. Float arithmetic is unsafe for this:
`4.10 / 0.10` is `40.99999999999999` in IEEE-754, which stalled the ladder cursor
and silently emitted a single point.

The rule-based baseline uses the same ladder, so the PRD 9.3 comparison is drawn
from one price universe rather than flattering either side.

---

## D-05 — Competitor data lives in the platform, not the Commerce Service

**Decision affected:** clarifies D13/D14

Competitor prices are third-party market observations, not the retailer's own
records. Putting them in `commerce.db` would collapse two of the three tiers the
problem statement asks us to separate. They are generated by
`pricing/feeds/competitor.py` behind the `CompetitorFeed` protocol, so a live
scraping or vendor-API adapter drops in without touching any caller.

---

## D-06 — LangGraph wraps the pipeline; the standalone path remains supported

**Decision affected:** D2 (LangGraph) — **now implemented**

`pricing/pipeline/graph.py` compiles the five stages into a `StateGraph` with a
SQLite checkpointer (NFR-024) and one conditional edge — the quality gate, which
branches on a boolean the deterministic checker already computed. No model
decides control flow anywhere in that file.

**Every node delegates to the same function `orchestrator.py` exposes.** There is
exactly one implementation of each stage, so the graph cannot drift from the
standalone path, and `USE_LANGGRAPH=false` changes checkpointing and tracing but
never the prices produced. The fallback is not a workaround; it is the property
that makes the graph a wrapper rather than a second implementation.

The human approval gate is deliberately *not* a mid-graph interrupt. It sits
after persistence, where the review queue is: the graph's job is to produce
recommendations, and who approves them is the autonomy model's decision, made
once the run is durable.

---

## D-08 — A market clock on the Commerce Service, for the feedback loop

**Decision affected:** none (addition required to make FR-055/FR-107–111 demonstrable)

Sales history is generated up to today. The moment the platform pushes a price
there is, by construction, no *future* in the dataset to observe — so "realized
outcome" would be permanently empty and the closed feedback loop could only ever
be demonstrated as a schema rather than as behaviour.

`commerce/routes/market.py` adds `POST /market/advance`, which generates
transactions for the next N days at whatever the price book currently holds,
using the same generative model as the seed: ground-truth elasticity,
seasonality, weekday shape, multiplicative noise. A retailer's market responding
to a price change is the Commerce Service's business, not the platform's.

Two properties keep the evaluation honest:

* **The platform still never reads ground truth.** It observes ordinary sales
  rows through `/sales` and must infer the response, exactly as in reality.
* **Advancing is idempotent per date.** A day that already transacted is not
  regenerated, so calling it twice does not double-count demand.

Namespaced under `/market` so its presence in a trace is unambiguous. It is a
demonstration affordance and nothing in the pricing pipeline depends on it.

---

## D-09 — Readback excludes promotion days from the pre-change window

**Decision affected:** none (defect found during verification)

The first working readback reported a *systematic* −20% to −39% forecast error
across every measured SKU. That uniformity was the tell: a genuine forecasting
miss is not one-directional on every item.

The cause is the same confound `analytics/elasticity.py` exists to control for.
A promotion cuts price **and** buys display space, so promoted days carry a
visibility lift on top of the price response. Comparing a promo-contaminated
baseline window against a clean post-change window attributes the lost display
lift to our own price move.

This mattered twice over: it biased the reported error, and it fed that bias
straight into the elasticity refinement — the loop would have "learned" from an
artefact. `services/feedback.py` now excludes promoted days from the
before-window, falling back to the contaminated series only when a SKU was on
promotion throughout.

Measured effect: mean absolute forecast error 28.9% → 12.6%.

---

## D-07 — Ground truth is served, never read by the pipeline

**Decision affected:** implements FR-002 safely

`commerce/routes/evaluation.py` exposes ground-truth elasticity at
`/eval/ground-truth` for the scoring harness. `pricing/clients/commerce.py`
deliberately has **no wrapper for it**, and says so in the module docstring.
Reading the answer key mid-run would make the accuracy metric meaningless.

---

## D-10 — The gateway API key is persisted, encrypted, rather than memory-only

**Requirement affected:** NFR-011

NFR-011 has two clauses. The first — credentials never reach the client and are
never written to disk **in plaintext** — is unchanged and now tested directly
(`tests/test_gateway_settings.py`). The second — "runtime-supplied keys stay in
memory" — is deliberately no longer met.

Memory-only meant the drawer's most consequential field silently emptied itself
on every restart. An operator configured the gateway, restarted for an unrelated
reason, and narration went quiet with no visible cause: `/api/config` reported
the URL it had persisted all along, so the settings *looked* correct. A
protection whose observable effect is a confusing outage gets worked around —
usually by putting the key back in `.env`, in plaintext, permanently, which is
strictly worse than what it was avoiding.

So the key is stored, encrypted (`pricing/core/secrets.py`: an HMAC-SHA256
keystream with encrypt-then-MAC, stdlib only — no new dependency to fetch
through a proxy). The ciphertext lives in `data/app.db`; the key that decrypts
it lives in `data/.secret.key`, git-ignored, owner-only where the filesystem
honours it. Copying the database — the routine way a credential escapes, via a
backup or a bug report — yields nothing.

**What this does not defend against:** anyone who can already read this user's
home directory reads both files. On a single-user workstation deployment
(NFR-001) nothing available would change that. The threat addressed is the
database leaving the machine, and for that the separation is sufficient.

The UI reflects the change rather than hiding it: the drawer states that the key
is stored encrypted and survives a restart, and shows an eight-character
fingerprint so a stale stored credential is distinguishable from a freshly
pasted one — without the key itself ever reaching the browser (FR-072).
