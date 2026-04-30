# F1 AI Orchestrator — Changelog

> **Project:** F1 AI Orchestrator — Virtual Pit Wall  
> **Stack:** Google ADK · Gemini 2.5 Flash · AlloyDB · FastF1 · Vertex AI · Cloud Run · React + Vite

---

# v3 — React UI + Agent Hardening (April 2026)

> **Baseline:** v2 final (commit `77c49ab`)  
> **Release:** Hackathon final demo (April 2026)

## Summary

v3 adds a complete F1-themed React frontend, hardens agent routing with 6 worked examples, fixes SQL generation reliability, adds structured logging across all agents, and deploys the UI from the same Cloud Run container.

---

## New: React UI (`f1-ui/`)

A full F1-themed chat interface built with React 19 + Vite 6, served from Cloud Run at `/ui/`.

| Feature | Detail |
|---|---|
| Live agent steps | SSE events parsed for `transferToAgent` + `functionCall` — shows `→ Intelligence Officer` · `Querying AlloyDB…` in real time while thinking |
| Agent attribution badges | Colour-coded pills per specialist: Intel (blue) · Analysis (amber) · Steward (purple) · Scheduler (green) |
| Streaming responses | Text rendered progressively as SSE chunks arrive; pulsing red dot indicator |
| Contextual follow-up chips | Year-aware suggestions after each response (e.g. "2024 Driver Standings" not "Show standings") |
| Session persistence | `localStorage` session ID — resumes last session on page load; `+ New Session` button resets |
| Copy button | Appears on hover for every assistant response |
| Retry button | Shown on connection errors with the original query pre-filled |
| 5-min abort timeout | `AbortController` kills hung requests after 5 min with a clear retry message |
| Horizontal table scroll | Wide tables scroll within the bubble instead of clipping |
| Literal `\n` fix | Pre-processes text before `marked.parse()` — handles Gemini's occasional newline escaping |

**Deployment:** Multi-stage Docker build (`node:20-slim` builds `f1-ui/dist`, Python stage copies it). `vite.config.js` uses `base: '/ui/'`. `main.py` mounts `StaticFiles` at `/ui` when `dist/` exists.

---

## Agent Routing: Coordinator Worked Examples

Replaced the single worked example with **6 scenario examples** covering all routing patterns. Each example shows the exact agent sequence, data source, and tool to use:

| # | Query type | Route |
|---|---|---|
| 1 | Past race result | Intel → `query_f1_db` |
| 2 | Penalties frequency | Intel → `query_f1_db` GROUP BY `f1_decisions` |
| 3 | Telemetry analysis | Analysis → `fetch_f1_telemetry` |
| 4 | Incident + standings | Steward → Intel (sequenced) |
| 5 | Next race + predict + calendar | Intel (`get_f1_schedule`) → Analysis (past AlloyDB) → Scheduler |
| 6 | Calendar only | Scheduler → `get_f1_schedule` |

---

## SQL & Schema Reliability

- **Removed banned table name list** from `SQL_GUIDELINES` and `schema.py` — listing Ergast names was causing LLM confusion
- **Replaced with positive guidance only** — valid tables + columns, preferred tools for standings/schedule
- **SQL rule 8:** on `query_f1_db` error → fall back to FastF1, do NOT retry with guessed names
- **`schema.py` TOOL ROUTING section:** `get_f1_schedule` for upcoming races · `get_f1_standings` for standings · FastF1 fallback on DB error

---

## Agent & Tool Fixes

| Fix | Detail |
|---|---|
| `before_agent_callback` on all agents | `[AGENT: name] started processing` log line — all 6 agents visible in Cloud Run logs |
| `get_f1_schedule` added to analysis agent | Was crashing with "Tool not found" on next-race predictions |
| `get_f1_standings` added to analysis agent | Needed for future race winner predictions |
| Analysis agent prediction rule | "Never fetch results for a race that hasn't happened" — FastF1 still allowed for past circuit data |
| Steward INCONCLUSIVE verdict | When no DB record found: outputs `INCONCLUSIVE` with explicit data gap labels, never fabricates VIOLATION/NO VIOLATION |
| `_to_md_table` column title case | `full_name` → `Full Name`, `avg_speed` → `Avg Speed` — DB column names no longer leak to users |
| Calendar fallback message | `📅 Direct calendar addition isn't available…` instead of `⚠️ Direct calendar write failed` |

---

## Infrastructure

| Change | Detail |
|---|---|
| Cloud Run timeout | 600s → 3600s (max) — complex FastF1 + multi-agent queries were timing out |
| CORS | `allow_origins=["regex:http://localhost:[0-9]+"]` — all localhost ports for local dev |
| Logging module | `logging.basicConfig` + `logger.info` replacing raw `print()` for structured Cloud Run logs |
| Dockerfile | Multi-stage build: Node 20 builds React, Python copies `dist/` — no Node in production image |

---

## File Changes (v3)

| File | Changes |
|---|---|
| `f1_orchestrator/agent.py` | 6 coordinator examples, SQL guidelines cleanup, agent callbacks, tool list fixes, steward confidence rules, `_to_md_table` title case, calendar messages |
| `f1_orchestrator/schema.py` | Removed banned names, added TOOL ROUTING section |
| `main.py` | CORS config, `StaticFiles` mount for React UI |
| `Dockerfile` | Multi-stage Node + Python build |
| `f1-ui/` | New — full React + Vite frontend (App, api, Header, MessageList, MessageBubble, InputBar, theme.css) |
| `f1-ui/vite.config.js` | `base: '/ui/'` for production serving |
| `README.md` | Full rewrite with architecture diagrams, Mermaid flowcharts, sequence diagram, RAG pipeline, performance table |

---

# v2 — Technical Specification (April 2026)

> **v1 baseline:** Hackathon submission (commit `1712777`)  
> **v2 release:** Final round refinement (April 2026)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture — Before vs After](#2-architecture--before-vs-after)
3. [Bug Fixes](#3-bug-fixes)
4. [New Capabilities](#4-new-capabilities)
5. [Database — Before vs After](#5-database--before-vs-after)
6. [Tool Reference — Before vs After](#6-tool-reference--before-vs-after)
7. [Infrastructure — Before vs After](#7-infrastructure--before-vs-after)
8. [Performance Impact](#8-performance-impact)
9. [File Changes](#9-file-changes)

---

## 1. Executive Summary

v2 is a ground-up hardening and feature expansion of the original hackathon submission. The core architecture moves from a flat single-tier swarm to a two-tier orchestration hierarchy, adds a new FIA Steward AI agent powered by a 5,525-chunk pgvector RAG pipeline, wires a Monte Carlo pit strategy simulator backed by Vertex AI Agent Engine, and fixes six production-grade bugs present in v1.

| Dimension | v1 | v2 |
|---|---|---|
| Agent tiers | 1 | 2 |
| Specialist agents | 3 | 5 |
| Tools | 8 | 17 |
| DB tables | 6 | 12 |
| RAG knowledge base | None | 5,525 regulation chunks + 664 steward decisions |
| Vector search | None | AlloyDB ScaNN (cosine, 768-dim) |
| Monte Carlo simulation | None | Vertex AI Agent Engine sandbox |
| FastF1 cache | Ephemeral (`/tmp`) | Persistent (GCS bucket, survives restarts) |
| DB connections | Per-query (new connection every call) | Pooled (`ThreadedConnectionPool`, min=2 max=10) |
| Credential refresh | Once at module load | Per-request (thread-safe) |
| SQL injection protection | None | Blocklist validator |
| Cross-agent data sharing | None | `ToolContext.state` session cache |

---

## 2. Architecture — Before vs After

### v1 — Single-Tier Swarm

```
f1_orchestrator  (root — routes to one specialist, no chaining)
├── f1_intel_agent       — DB + FastF1 + telemetry + pit + weather
├── f1_analysis_agent    — telemetry + pit + weather + DB
└── f1_event_scheduler   — schedule + calendar
```

**Problems:**
- `fetch_f1_telemetry`, `fetch_f1_pit_strategy`, `fetch_f1_technical_details` existed on **both** `f1_intel_agent` and `f1_analysis_agent` — the orchestrator LLM could route to either unpredictably
- Multi-domain queries (e.g. "telemetry + standings") had no multi-agent coordination path — the orchestrator could only pick one specialist
- No data sharing between agents — if intel loaded a FastF1 session, analysis loaded it again independently

---

### v2 — Two-Tier Hierarchy

```
race_strategist  (Tier 1 — classifies + routes, no tools)
    │
    └── f1_coordinator  (Tier 2 — sequences agents, shares state)
            ├── f1_intel_agent      — structured data only (no telemetry tools)
            ├── f1_analysis_agent   — telemetry + strategy + Monte Carlo
            ├── f1_steward_agent    — FIA regulations RAG + steward precedent  ← NEW
            └── f1_event_scheduler  — calendar only (always isolated)
```

**Key design decisions:**
- ADK enforces one parent per agent instance — specialists are children of `f1_coordinator` only; orchestrator routes exclusively through the coordinator
- `ToolContext.state` is session-scoped and propagates across sub-agent invocations — data written by intel is readable by analysis without re-fetching
- `f1_event_scheduler` never reads or writes state — it is always called last in any coordinator sequence and is fully isolated

---

## 3. Bug Fixes

### BUG-01 — Tool Duplication (Routing Ambiguity)

| | Detail |
|---|---|
| **Location** | `agent.py:313-319` (intel tools) + `agent.py:383-389` (analysis tools) |
| **Problem** | `fetch_f1_telemetry`, `fetch_f1_pit_strategy`, `fetch_f1_technical_details` registered on both `f1_intel_agent` and `f1_analysis_agent`. The orchestrator LLM routing was non-deterministic. |
| **Fix** | Removed all three from `f1_intel_agent.tools`. Analysis agent owns them exclusively. |

---

### BUG-02 — Connection Per Query (~200–400ms overhead)

| | Detail |
|---|---|
| **Location** | `agent.py:54` |
| **Problem** | `psycopg2.connect(...)` called inside `query_f1_db` on every SQL execution — a new TCP connection to AlloyDB on every tool call. |
| **Fix** | Module-level `ThreadedConnectionPool` (`_get_pool()`), `minconn=2`, `maxconn=10`. Connections returned to pool after each query. |

```python
# v1 — new connection every call
def query_f1_db(sql_query: str):
    conn = psycopg2.connect(...)   # 200-400ms overhead
    ...

# v2 — pooled
def query_f1_db(sql_query: str, tool_context: ToolContext = None):
    conn = _get_pool().getconn()
    try: ...
    finally: _get_pool().putconn(conn)
```

---

### BUG-03 — Ephemeral FastF1 Cache (Lost on Every Cold Start)

| | Detail |
|---|---|
| **Location** | `agent.py:15` |
| **Problem** | `CACHE_DIR = '/tmp/fastf1_cache'` — Cloud Run `/tmp` is per-instance ephemeral storage. Every cold start re-downloaded all FastF1 session data. |
| **Fix** | `CACHE_DIR = os.getenv("FASTF1_CACHE_DIR", '/tmp/fastf1_cache')` — on Cloud Run, `FASTF1_CACHE_DIR` points to `/mnt/gcs-cache/fastf1` (GCS FUSE mount, persists across restarts). |

---

### BUG-04 — Stale Credentials (Calendar Breaks After 1 Hour)

| | Detail |
|---|---|
| **Location** | `agent.py:30-38` |
| **Problem** | `credentials.refresh()` called once at module load. OAuth tokens expire after 1 hour. All calendar invites after the first hour silently failed. Additionally, the module-level `credentials` object was shared across concurrent requests — mutating it in one request broke others. |
| **Fix** | Removed module-level credential block entirely. `send_f1_calendar_invite` calls `google.auth.default()` and `credentials.refresh()` per invocation — fresh credentials, thread-safe. |

```python
# v1 — refreshed once at import, shared across threads
credentials, project_id = google.auth.default(scopes=[...])
if not credentials.valid:
    credentials.refresh(...)  # never called again

# v2 — fresh per call
def send_f1_calendar_invite(...):
    creds, _ = google.auth.default(scopes=[...])
    if not creds.valid or creds.expired:
        creds.refresh(google.auth.transport.requests.Request())
    service = build('calendar', 'v3', credentials=creds)
```

---

### BUG-05 — Static Date Context (Stale If Service Runs Overnight)

| | Detail |
|---|---|
| **Location** | `agent.py:20` |
| **Problem** | `get_current_context()` was called once at module import time and baked into every agent's instruction string. If the Cloud Run instance stayed warm across midnight, agents would reason with yesterday's date. |
| **Fix** | Replaced with `get_temporal_context(tool_context)` — a tool that agents call at the start of each request, writes `current_date` to session state, and returns the live date. |

---

### BUG-06 — SQL Injection

| | Detail |
|---|---|
| **Location** | `agent.py:62` |
| **Problem** | `cur.execute(sql_query)` executed raw LLM-generated SQL with no validation. A prompt injection could issue a `DROP TABLE` or `DELETE FROM`. |
| **Fix** | `_validate_sql()` blocklist check before execution. Also added `_query_raw(sql, params)` for all internal parameterised queries — eliminates interpolation risk on internal SQL. |

```python
BLOCKED_SQL = ['DROP','DELETE','INSERT','UPDATE','ALTER','TRUNCATE','CREATE','GRANT']

def _validate_sql(sql: str) -> bool:
    return not any(kw in sql.upper() for kw in BLOCKED_SQL)

def query_f1_db(sql_query: str, ...):
    if not _validate_sql(sql_query):
        return "SQL Error: Only SELECT queries are permitted."
    ...
```

---

## 4. New Capabilities

### 4.1 Cross-Agent Data Sharing (`ToolContext.state`)

Every tool function now accepts `tool_context: ToolContext` and caches its result under a structured key. Subsequent calls with identical arguments — from the same agent or a different one — hit the cache and skip the network fetch.

```python
cache_key = f"session_{year}_{gp_name}_{session_type}"
if tool_context and cache_key in tool_context.state:
    return tool_context.state[cache_key]   # instant, no FastF1 fetch
# ... fetch ...
tool_context.state[cache_key] = result    # written once, shared
```

**Verified:** `scripts/test_state_sharing.py` confirms state keys written by one agent are visible to subsequent agents in the same session.

---

### 4.2 FIA Steward Agent

New `f1_steward_agent` validates racing incidents against FIA regulations and historical precedent.

**Workflow:**
1. `fetch_race_control_messages` — gets flag status and incidents for the race
2. `query_f1_db` — retrieves lap-level validity data for involved drivers
3. `query_f1_regulations` — semantic search over 5,525 regulation chunks to find the applicable FIA article
4. `query_steward_decisions` — semantic search over 664 historical decisions for precedent
5. Returns a structured verdict: **VIOLATION / NO VIOLATION / INCONCLUSIVE**, citing article number and precedent

**Output format:**
```
## Steward Review: [Incident]
### Race Control at Time of Incident
### Relevant Regulation — Article X.Y
### Precedent Table
### Verdict — Finding + Reason + Likely Penalty
```

---

### 4.3 pgvector RAG Pipeline

**Regulations** — 17 FIA PDFs (2021–2026) parsed with pypdf, chunked by article heading, embedded with `text-embedding-005` (768-dim), and stored in `f1_regulations` with a ScaNN cosine index.

| Year | Sporting | Technical | Financial | General | Total |
|---|---|---|---|---|---|
| 2021 | 347 | 385 | 159 | — | 891 |
| 2022 | 400 | 531 | 113 | — | 1,044 |
| 2023 | 392 | 538 | 112 | — | 1,042 |
| 2024 | 403 | 542 | 112 | — | 1,057 |
| 2025 | 418 | 545 | — | — | 963 |
| 2026 | 108 | 325 | — | 95 | 528 |
| **Total** | | | | | **5,525** |

**Steward Decisions** — 664 decisions from OpenF1 race control messages (2023–2026, 74 race weekends), parsed for driver, incident type, penalty, and article reference, embedded and indexed in `f1_decisions`.

---

### 4.4 Monte Carlo Pit Strategy Simulator

`f1_analysis_agent` now includes an `AgentEngineSandboxCodeExecutor` backed by a Vertex AI Agent Engine reasoning engine (`reasoningEngines/6007806850714566656`).

When asked for optimal pit strategy:
1. Fetches stint data via `fetch_f1_pit_strategy`
2. Writes a Python simulation (1000 iterations) with parameters: tyre deg rates per compound, pit loss time, safety car probability
3. Executes in the sandbox
4. Reports optimal pit lap, 1-stop vs 2-stop delta, and confidence interval

The executor is `None`-safe — if the sandbox is unavailable, the agent falls back to a reasoned estimate from stint data.

---

### 4.5 New Tools

| Tool | Agent | Description |
|---|---|---|
| `get_circuit_characteristics` | Intel | AlloyDB lookup for circuit metadata + race history |
| `get_driver_head_to_head` | Intel | Career or season-scoped head-to-head comparison |
| `get_session_times` | Scheduler | All session start times for a GP weekend (FP1 → Race) |
| `fetch_race_control_messages` | Steward | AlloyDB-first, FastF1 fallback for race control data |
| `query_f1_regulations` | Steward | pgvector semantic search over FIA regulations |
| `query_steward_decisions` | Steward | pgvector semantic search over steward decisions |
| `get_temporal_context` | All | Live date injection per request (replaces static `get_current_context()`) |

---

### 4.6 AlloyDB-First Tool Fallback with Write-Back

`fetch_f1_telemetry` and `fetch_f1_pit_strategy` now follow a three-tier resolution:

```
1. ToolContext.state cache    → instant (no DB hit)
2. AlloyDB pre-aggregated     → ~10ms (no FastF1 API call)
3. FastF1 live fetch          → 2–30s (writes result back to AlloyDB for next time)
```

The write-back ensures that any session fetched live is automatically persisted — subsequent queries for the same session use AlloyDB even if the backfill hasn't processed it yet.

---

### 4.7 Calendar Improvements

| Feature | v1 | v2 |
|---|---|---|
| Sessions per invite | 1 | Up to 6 (full weekend via `get_session_times`) |
| Duration parameter | Hardcoded 2h | `duration_hours` (Race=2h, Quali/Practice/Sprint=1h) |
| Reminders | None | 1-hour popup + 1-day popup |
| Credential bug | Breaks after 1h | Fixed (per-request refresh) |
| Sprint weekends | Not handled | Sprint + Sprint Qualifying as separate invites |

---

## 5. Database — Before vs After

### v1 Schema (6 tables)
```
f1_results    f1_drivers    f1_teams
f1_sessions   f1_circuits   f1_standings
```

### v2 Schema (12 tables)

```
── Core (unchanged) ──────────────────────────────
f1_results       f1_drivers       f1_teams
f1_sessions      f1_circuits      f1_standings

── Telemetry Layer (new) ─────────────────────────
f1_telemetry_summary    Pre-aggregated fastest-lap stats per driver/session
f1_stints               Pit stop stint data (compound, lap range)
f1_lap_summary          Lap-by-lap validity + sector times
f1_race_control         Flags, SC, VSC, penalty messages

── RAG Layer (new) ───────────────────────────────
f1_regulations          5,525 FIA article chunks + vector(768) embeddings
f1_decisions            664 steward decisions + vector(768) embeddings
```

**New indexes:**
- `regulations_embedding_idx` — ScaNN cosine, `num_leaves=50`
- `decisions_embedding_idx` — ScaNN cosine, `num_leaves=30`

**New extensions:**
- `vector` — pgvector for embedding storage
- `alloydb_scann` — approximate nearest-neighbour search
- `google_ml_integration` — in-database Vertex AI embedding generation

---

## 6. Tool Reference — Before vs After

### v1 Tools (8 total)

| Tool | Agent(s) | Source |
|---|---|---|
| `query_f1_db` | Intel + Analysis | AlloyDB (new connection per call) |
| `fetch_fastf1_live_data` | Intel + Analysis | FastF1 live |
| `get_f1_schedule` | Intel + Scheduler | FastF1 live |
| `get_f1_standings` | Intel | AlloyDB (SQL injection risk) |
| `fetch_f1_telemetry` | **Intel + Analysis** ⚠️ | FastF1 live |
| `fetch_f1_pit_strategy` | **Intel + Analysis** ⚠️ | FastF1 live |
| `fetch_f1_technical_details` | **Intel + Analysis** ⚠️ | FastF1 live |
| `send_f1_calendar_invite` | Scheduler | Google Calendar API |

⚠️ = duplicated across agents

### v2 Tools (17 total)

| Tool | Agent | Source | State Cache |
|---|---|---|---|
| `get_temporal_context` | All | System clock | Writes `current_date` |
| `query_f1_db` | Intel + Analysis + Steward | AlloyDB (pooled, validated) | — |
| `fetch_fastf1_live_data` | Intel + Analysis | FastF1 → AlloyDB fallback | ✓ |
| `get_f1_schedule` | Intel + Scheduler | FastF1 | ✓ |
| `get_f1_standings` | Intel | AlloyDB (parameterised) | ✓ |
| `get_circuit_characteristics` | Intel | AlloyDB | ✓ |
| `get_driver_head_to_head` | Intel | AlloyDB | ✓ |
| `fetch_f1_telemetry` | Analysis only | AlloyDB → FastF1 + write-back | ✓ |
| `fetch_f1_pit_strategy` | Analysis only | AlloyDB → FastF1 + write-back | ✓ |
| `fetch_f1_technical_details` | Analysis only | FastF1 | ✓ |
| `get_session_times` | Scheduler | FastF1 | ✓ |
| `send_f1_calendar_invite` | Scheduler | Google Calendar API | — |
| `query_f1_regulations` | Steward | AlloyDB pgvector ScaNN | ✓ |
| `query_steward_decisions` | Steward | AlloyDB pgvector ScaNN | ✓ |
| `fetch_race_control_messages` | Steward | AlloyDB → FastF1 fallback | ✓ |

---

## 7. Infrastructure — Before vs After

| Component | v1 | v2 |
|---|---|---|
| Cloud Run memory | 2Gi | 4Gi (FastF1 + telemetry processing is memory-heavy) |
| Cloud Run execution env | gen1 | gen2 (required for GCS FUSE) |
| FastF1 cache | `/tmp` (ephemeral) | GCS FUSE mount at `/mnt/gcs-cache/fastf1` |
| GCS buckets | None | `f1-command-center-dev-f1-cache` + `f1-command-center-dev-f1-regulations` |
| Embedding service | None | Vertex AI `text-embedding-005` (768-dim) |
| Simulation compute | None | Vertex AI Agent Engine sandbox |
| IAM roles added | 3 | 8 (+ `documentai.apiUser`, `storage.objectAdmin`, `secretmanager.secretAccessor`, `aiplatform.user`) |
| Deployment method | `gcloud run deploy --source` | `gcloud run services replace cloudrun-service.yaml` (full spec control) |

---

## 8. Performance Impact

| Query Type | v1 | v2 | Improvement |
|---|---|---|---|
| DB query overhead | ~200–400ms connection setup per call | ~5–10ms (pooled) | **20–40× faster** |
| Repeated telemetry fetch (same session) | Full FastF1 reload each time | State cache hit (instant) | **~30s → 0s** |
| Historical telemetry (post-backfill) | FastF1 live fetch (~5–30s) | AlloyDB pre-aggregated (~10ms) | **500–3000× faster** |
| Regulation lookup | Not available | pgvector ScaNN (~5ms) | **New capability** |
| Steward precedent | Not available | pgvector ScaNN (~5ms) | **New capability** |
| Calendar after 1h uptime | Broken (stale credentials) | Always works | **Bug fix** |
| Cold start cache | Empty (all re-fetched) | GCS persistent (warm) | **~30s → 0s per cached session** |

---

## 9. File Changes

### Modified
| File | Changes |
|---|---|
| `f1_orchestrator/agent.py` | Full rewrite — pooling, credentials, state caching, 9 new tools, 2 new agents, updated routing |
| `f1_orchestrator/schema.py` | Added 6 new table definitions (telemetry + RAG layers) |
| `README.md` | Updated for two-tier architecture, full capability docs |
| `f1_orchestrator/data_dictionary.md` | Added all 6 new tables with full column reference + query examples |

### New Files
| File | Purpose |
|---|---|
| `cloudrun-service.yaml` | Full Cloud Run service spec — GCS FUSE, 4Gi, all env vars, secrets |
| `scripts/run_migrations.py` | AlloyDB DDL runner (schema + ScaNN indexes), supports `--dry-run` and `--indexes` |
| `scripts/backfill_telemetry.py` | FastF1 → AlloyDB historical backfill (2020–2025), rate-limit aware, resume-safe |
| `scripts/ingest_regulations.py` | FIA PDF → pypdf → embeddings → `f1_regulations` pipeline |
| `scripts/build_steward_decisions.py` | OpenF1 race control → structured decisions → embeddings → `f1_decisions` pipeline |
| `scripts/test_state_sharing.py` | ADK `ToolContext.state` propagation verification test |
| `scripts/setup_iam.sh` | One-shot IAM role setup for Cloud Run service account |
| `scripts/deploy.sh` | One-command deploy: Cloud Build → patch YAML → `gcloud run services replace` |
| `scripts/requirements-ingest.txt` | Separate pip requirements for ingestion scripts (avoids protobuf conflict) |
| `PENDING.md` | Tracks remaining tasks and billing-dependent steps |
| `CHANGELOG.md` | This document |

---

*v2 — April 2026 · Stack: Google ADK 1.28.1 · Gemini 2.5 Flash · AlloyDB pgvector · FastF1 3.8.2 · Vertex AI · Cloud Run gen2*
