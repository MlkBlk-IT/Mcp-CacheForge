# IcyCache — Release notes

> This file is the release annotation for each published version. It is maintained in English;
> the rest of the repository (code, comments, internal docs) is written in French.

---

## v0.3.1 — 2026-10-26

**The install instructions were not true yet.**

### Fixed

- **Nothing was published on PyPI or npm.** `pip install icycache` and `npx -y icycache-mcp` both
  returned a 404 while the README presented them as working commands. The README now says so
  explicitly and gives the install-from-source command that works today:
  `pip install "icycache @ git+https://github.com/MlkBlk-IT/icycache.git"`.
- Unlike `loom-mcp` (which belongs to other projects on both registries), the names `icycache` and
  `icycache-mcp` are **free** on PyPI and npm — so these commands are the final ones and will work
  as written once the first publish happens.

### Changed

- Version bumped to 0.3.1 — `pyproject.toml` and `package.json` now match the `v0.3.1` tag and this release.
- The npm package is named **`icycache`**, the same name as the repository. It exposes two binaries:
  `icycache` (the CLI: `serve` / `mcp` / `doctor`) and `icycache-mcp` (the MCP server on stdio), so
  `npx -y icycache` and `npx -y -p icycache icycache-mcp` both resolve to this project. The PyPI
  distribution stays **`icycache`** too (`pip install icycache`).

### Not changed

No code change: the gateway, the MCP layer, the semantic cache and the normalization engine are
exactly as shipped in v0.3.0.

---

## v0.3.0 — 2026-10-26

**DELTA-002** — one correction pass over the request path, covering BUG-001, BUG-002 and DELTA-001.

### Added
- **Resilient upstream client** (`src/upstream_client.py`) — one client pool per base URL, with
  drain → recycle → retry. Fixes **BUG-001** (gateway stuck returning `502` after a `400`).
- **Opt-in semantic cache** (`src/icy_semantic.py`, `config/semantic_cache.json`) — embeddings +
  Qdrant store, with a silent miss when the embedder or Qdrant is unavailable.
- **Tool registry** (`config/tools_registry.json`) — the 100 verified MCP tool anchors from
  DELTA-001, asserted at startup (`total == 100`).
- **Number normalization** — `"two plus two"` → `2+2`. 11 new rules: **1111 → 1122**.
- **Tests** — `tests/test_semantic_cache.py`, `tests/test_upstream_recovery.py`, `tests/test_anchors.py`.
- **Docs** — `docs/BUG-001-502-stuck-after-400.md`, `docs/BUG-002-no-semantic-cache.md`,
  `docs/DELTA-001-tool-proxy-top100.md`, `docs/DELTA-002-unified-fix.md`.

### Changed
- `src/mcp_server.py` — loads the semantic cache config and the tools registry, wires the
  resilient upstream pool.
- `src/prefix_proxy.py` — recycles the httpx client and retries once on an upstream error.
- `src/icy_normalize.py` — number-word → digit conversion, before accents/punctuation stripping.
- `config/normalization_rules.json` — 11 rules added, `total_rules` metadata corrected to 1122.
- `README.md` — rewritten in English, counts aligned with the code (1122 rules, 18 upstreams,
  11 MCP tools), semantic-cache status documented as opt-in.

### Removed
- Legacy pre-rename artefacts: `CacheForge_Project_Documentation.docx`, `start_cacheforge.ps1`,
  `_loop3d_new.js`, `tools/apply_fixes*.py`, `tools/add_live_sse.py`, `tools/prune_legacy_2d.py`,
  `tools/read_live_capture.py`.
- Stale dashboard captures and the legacy 2D canvas assets under `static/`.

### Honest status
- The **semantic cache is implemented but disabled by default** (`semantic_enabled: false`).
  Turning it on requires an embedding endpoint (LM Studio on `:1234`) and a running Qdrant.
- The gateway still refuses to start if another service already holds port `8080`.

---

## v0.2.0 — 2026-10-24

### Added
- New universal gateway `src/server.py` (port 8080) with the shared core `src/icy_core.py`.
- Standalone normalization engine `src/icy_normalize.py`, cache store `src/icy_cache_store.py`.
- MCP facade `src/mcp_gateway.py`, stdio bridge `src/mcp_stdio_bridge.py`.
- MCP tool proxy `src/tool_proxy.py`, upstream helpers `src/upstream_utils.py`.
- Isolated gateway tests `tests/test_gateway.py`.
- `tools/` scripts, packaging (`pyproject.toml`, `package.json`), gateway config
  `config/icycache.gateway.json`, autostart and update scripts.

### Changed
- `src/server.py` — MCP exposure + documentation (+350 lines).
- `README.md` — rebrand and documentation.
- `static/dashboard.html` — dashboard redesign (live nodes, electric gauge, cyberpunk palette).
- `tray.py`, `.ps1` scripts, `requirements.txt`, `config/icycache.json`.

### Deliberately excluded
- PNG captures (`static/*.png`) and the `backup_pre_gateway/` folder — local artefacts, not versioned.
