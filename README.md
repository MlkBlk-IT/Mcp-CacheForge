> ## ⚠️ NOTICE — The rest of this repository (code, comments, scripts, docs, commits, issues) is written in **French**. Only this README is in English.
> ⚠️ WORK IN PROGRESS — migration in progress (2026-10-24)
> IcyCache has been renamed from CacheForge. The **universal multi-upstream gateway** (`src/server_new.py`) is
> written and tested, but it runs **alongside** `src/server.py`: the swap on port 8080 will happen
> once the dashboard is finalized, then the two will be unified.
> - `src/server.py` remains the active service on `:8080` / `:8081` / `:8084` (unchanged, authoritative).
> - The gateway is ready to start on its own: `python src/server_new.py --port 8090`.
> - `src/server.py` and `static/dashboard.html` are **not** modified by the gateway.
>
> ### Available MCP transports (all three active at once)
>
> | Transport | Endpoint | Target client |
> |-----------|----------|---------------|
> | **stdio** | `python src/server.py --mode mcp-stdio` | Claude Desktop, IDEs, any stdio client |
> | **SSE** (legacy) | `http://127.0.0.1:8081/sse` | Hermes, older clients |
> | **Streamable HTTP** (standard) | `http://127.0.0.1:8084/mcp` | Recent MCP clients, MCP Inspector |
>
> In stdio mode, go through `src/mcp_stdio_bridge.py`: it reserves stdout for JSON-RPC.
> (Nothing is written to stdout anymore, but the bridge remains the safety net.)

# 🔥 IcyCache — Universal LLM Gateway with local cache

> **"Forge the cache, not the answer."**
> HTTP gateway that sits between **any client** and **any upstream** (local or cloud),
> caches responses and serves them back **instantly** — no CPU regeneration.

---

## The problem

| Problem | Consequence |
|---------|-------------|
| **Latency** | An identical request burns CPU/RAM again for an already-computed answer |
| **No semantic cache** | Standard systems hash the raw prompt: `Explain Hyper-V` and `Please explain Hyper-V thanks` generate **two** distinct requests |
| **No observability** | Impossible to measure the tokens and time actually saved |
| **Traffic bypassing the proxy** | Every client talking directly to its provider breaks the cache promise |

IcyCache attacks all four: normalization before hashing, SQLite cache with TTL, exportable metrics,
and **a single entry point** for all LLM traffic.

---

## Target architecture

```
   Any client                 (Hermes, Claude Code, scripts, IDE, SDK, CLI)
   base_url = http://127.0.0.1:8080
              │
              ▼
┌───────────────────────────────────────────────────────────────────────┐
│  ICYCACHE GATEWAY  127.0.0.1:8080   (src/server_new.py)              │
│                                                                       │
│  ROUTING      URL prefix  /<upstream>/v1/...                          │
│               header      X-IcyCache-Upstream: lmstudio               │
│               body        "upstream": "ollama"                        │
│               default     gateway.default_upstream (config)           │
│                                                                       │
│  NORMALIZATION NFKD → lowercase → Aho-Corasick → accents → punctuation│
│  CACHE KEY     SHA256 of the NORMALIZED payload (+ upstream + endpoint)│
│  DECISION      HIT → instant response | MISS → forward + save         │
│  TOKENS        REAL usage from the upstream. Never an estimate.       │
│  OBSERVABLE    WebSocket /ws/events : live deltas + final             │
│  MCP (facade)  stats / clear / search / realtime / tool-cache         │
└───────────────────────────────────────────────────────────────────────┘
        │                    ▲                              │
        ▼                    │ same path back                │
 { OmniRoute | LM Studio | Ollama | OpenAI | Anthropic | … }
        │
        ▼
   SQLite db/cache.db   (events: upstream, real tokens, latencies, ttft)
```

**Rule: everything that leaves a client and comes back to it goes through IcyCache.**
OmniRoute is no longer the entry point: it is **one upstream among others**.

---

## Components

| File | Role |
|------|------|
| `src/server_new.py` | **Universal multi-upstream gateway** (FastAPI): routing, cache, real tokens, WebSocket |
| `src/mcp_gateway.py` | **MCP facade** of the gateway: 7 tools, tool-cache, loop guard, mirror REST API |
| `src/icy_core.py` | Shared core: DB (`events`, `agent_events`, `tool_cache`), WebSocket hub, loop guard |
| `src/icy_normalize.py` | Standalone normalization engine (faithful port, no import side effects) |
| `src/icy_cache_store.py` | LLM cache store (compatible `cache` table, additive `raw_hash` column) |
| `src/upstream_utils.py` | Usage normalization, headers, cache_control/prompt_cache_key mutation, SSE meter |
| `src/mcp_stdio_bridge.py` | stdio bridge: reserves stdout for MCP JSON-RPC, logs and prints to stderr |
| `src/server.py` | Legacy service (active on :8080/:8081) — **will be unified with the gateway** |
| `config/icycache.gateway.json` | Gateway config: upstreams, cache, loop guard, tool cache |
| `config/normalization_rules.json` | 1114 normalization rules |
| `tools/smoke_gateway.py` | **Real** multi-upstream smoke test (isolated ports, temp DB) |
| `tools/enforce_gateway.ps1` | Firewall rules: makes bypass impossible |
| `tools/verify_enforcement.py` | **Proof**: can the client still reach an upstream directly? |
| `tests/test_gateway.py` | 10 isolated tests (mock upstream, temp DB) |

---

## Routing: pick your upstream

| Method | Example | Priority |
|--------|---------|----------|
| URL prefix | `POST http://127.0.0.1:8080/lmstudio/v1/chat/completions` | 1 |
| Header | `X-IcyCache-Upstream: lmstudio` | 2 |
| Body | `{"upstream": "lmstudio", ...}` | 3 |
| Config default | `gateway.default_upstream` | 4 |

No **implicit** default in the code: without an explicit source, the request is rejected with
`400` listing the available upstreams. An unknown upstream is rejected with `404`.

---

## HTTP routes (gateway)

| Route | Method | Role |
|-------|--------|------|
| `/v1/chat/completions` | POST | OpenAI-compatible (streaming and non-streaming) |
| `/v1/messages` | POST | Native Anthropic (`kind: anthropic`) |
| `/v1/embeddings` | POST | Embeddings (cached) |
| `/v1/models` | GET | Upstream models |
| `/{upstream}/v1/{rest}` | * | Explicit routing by prefix |
| `/ws/events` | WS | Realtime events: live `delta` + `final` (real tokens) |
| `/health`, `/api/status` | GET | Health, reachable upstreams |
| `/api/metrics`, `/api/metrics/series`, `/api/metrics/export` | GET | Metrics (dashboard shapes) |
| `/api/agent-events` | GET | Agent / tool-cache events |
| `/api/cache-clear` | POST | Empty the cache — header `X-IcyCache-Confirm: FORCE_DELETE` |
| `/dashboard` | GET | Web dashboard (read-only) |

**Confirmation headers:** `X-IcyCache-Confirm` (unified naming). The old `X-CacheForge-Confirm`
is still accepted by the gateway for compatibility.

---

## Tokens: the rule of the real

| Source | Value |
|--------|-------|
| `usage` field of the upstream response | **always authoritative** |
| OpenAI streaming | `stream_options.include_usage=true` injected → usage from last chunk |
| Anthropic streaming | `message_start` + `message_delta` |
| Usage absent | `NULL` + `tokens_source: "n/a"` — **never an estimate** |
| HIT | `saved_prompt_tokens` / `saved_completion_tokens` = real usage of the 1st call |

`events` columns: `upstream`, `client`, `prompt_tokens`, `completion_tokens`, `cached_tokens`,
`cache_write_tokens`, `latency_ms`, `ttft_ms`.

---

## MCP tools (facade)

### Legacy server (`src/server.py`) — 11 tools

| Tool | Role |
|------|------|
| `get_cache_stats` | Entries, hits, breakdown by upstream/model |
| `clear_cache` | Empty the cache (`confirm='FORCE_DELETE'`) |
| `normalize_preview` | Preview of normalization + cache key |
| `search_cached_prompts` | Search in normalized prompts |
| **`proxy_tool`** | **Call a tool on a target MCP server** (tool calling) |
| **`proxy_tools`** | **Batch call of target tools** |
| **`list_proxy_servers`** | **Routable MCP servers** |
| **`list_proxy_tools`** | **Tools exposed by a target server** |
| `get_tool_cache_stats` | Tool-cache state + agent events |
| `clear_tool_cache` | Empty the tool cache (`confirm='FORCE_DELETE'`) |
| `check_tool_loop` | Detect an agent loop in an LLM history |

### Gateway server (`src/mcp_gateway.py`) — 11 tools

The 7 observability tools (`get_cache_stats`, `clear_cache`, `normalize_preview`,
`search_cached_prompts`, `get_realtime_tokens`, `list_upstreams`, `tool_cache_stats`)
**plus** the 4 relay tools (`proxy_tool`, `proxy_tools`, `list_proxy_servers`,
`list_proxy_tools`).

MCP is a **facade**: it observes and drives. It is not in the request path
(an MCP client calls its tools deliberately — you cannot intercept an
OpenAI/Anthropic stream with it). The request path is the HTTP gateway.

Agent loops: `icy_core.llm_loop_check()` detects the same tool called again with the same
arguments **and** the same results (Anthropic and OpenAI formats). Threshold: 3 repetitions = warning.

---

## Tool calling: safety rules

The tool proxy (`src/tool_proxy.py`) relays calls to target MCP servers.
Three non-negotiable rules:

1. **An MCP error is never cached** and never returns `ok: true`.
   An `isError: true` becomes `ok: false` with the real message (no opaque `ExceptionGroup`).
2. **Write tools are never cached.** Replaying a cached response
   must have no side effect; otherwise a write is silently skipped.
   - `cache_tools` present → **strict allowlist** (only tools listed as `true` are cached).
   - otherwise `cache: true` → **anti-write heuristic** (`create_`, `write_`, `delete_`, `push_`, … excluded).
   - otherwise → no cache.
3. **MCP sessions are persistent** (one process per server, not per call).
   Without this, every tool call respawned a process: 21s measured on `npx`, 12s on `uvx`.

### Example

```json
// via any MCP client
{"server": "github", "tool": "search_repositories", "args": {"query": "icycache"}}
// -> {"ok": true, "cached": false, "latency_ms": 4867, "result": {...}}
```

### Target server configuration

`config/icycache.json` → `tool_proxy.servers`. Two portability tokens:

| Token | Value |
|-------|-------|
| `{PROJECT_ROOT}` | project root (moving IcyCache needs no edit) |
| `{PYTHON}` | project venv interpreter (the only one with `fastapi`/`mcp`) |

On Windows, `.cmd` commands (like `npx`) are resolved automatically:
`CreateProcess` cannot launch an `npx.cmd` script without a shell.

---

## Enforcement: making bypass impossible

```powershell
# 1. Install the rules (ELEVATED PowerShell)
powershell -ExecutionPolicy Bypass -File tools\enforce_gateway.ps1 -Action apply `
    -TargetProgram "C:\Users\Tech\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe" `
    -AllowPort 8080 -BlockPorts 20128,1234,11434

# 2. The proof: can the client still reach an upstream directly?
python tools\verify_enforcement.py --program "D:\IcyCache\venv\Scripts\python.exe" --allow-port 8080
```

Strategy (Windows Firewall, per program): block upstream local ports **including on
127.0.0.1**, block all remote traffic, explicitly allow `127.0.0.1:8080`.
The `verify_enforcement.py` program runs **with the client binary** (rules are per program)
and checks that only IcyCache remains reachable.

---

## Environment variables (unified naming)

| Variable | Role | Old name (still read by `src/server.py` for `ICYCACHE_*`) |
|----------|------|----------------------------------------------------------|
| `ICYCACHE_DB` | Cache DB path | `CACHEFORGE_DB` |
| `ICYCACHE_LOG` | Log path | `CACHEFORGE_LOG` |
| `ICYCACHE_CONFIG` | Config file (gateway) | — |
| `ICYCACHE_TARGET` | Upstream override (isolated tests) | `CACHEFORGE_TARGET` |
| `ICYCACHE_TOOLPROXY_CONFIG` | Tool-proxy config (default: `config/icycache.json`) | — |

---

## Installation

```powershell
# 1. Environment (Python 3.12.10)
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install websockets        # required for /ws/events

# 2. Manual start
.\start_icycache.ps1

# 3. Start at login (Task Scheduler, or Startup fallback without admin)
.\install_autostart.ps1
```

---

## Tests

```powershell
# Isolated suite (mock upstream, temp DB, no port occupied)
.\venv\Scripts\python.exe -m pytest tests\test_gateway.py -v

# Isolated integration suite (temp DB + fake upstream :8190/:8191)
.\venv\Scripts\python.exe tests\run_isolated.py

# Offline PrefixProxy test (fake provider, no external call)
.\venv\Scripts\python.exe tests\test_prefix_proxy.py

# Real smoke test (LM Studio / OmniRoute, port 8090, temp DB)
.\venv\Scripts\python.exe tools\smoke_gateway.py --port 8090
```

The smoke test touches **neither** the production service (`:8080`) **nor** the dashboard.
`tests\run_isolated.py` uses a **temporary** database (`ICYCACHE_DB`) and never touches
the production database.

---

## License

MIT
