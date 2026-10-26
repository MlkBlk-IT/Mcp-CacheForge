# Installation — IcyCache

Universal LLM gateway with a local cache. Three ways to install; all of them
expose the same `icycache` console script.

| Method | Command | Use it when |
|---|---|---|
| uv (recommended) | `uv tool install icycache` | You want an isolated tool, no venv to manage |
| pip | `pip install icycache` | You already manage virtualenvs |
| npm | `npx icycache-mcp` | The MCP client is Node-only (Claude Desktop, Cursor, VS Code) |

The npm wrapper needs no Python at all if [uv](https://docs.astral.sh/uv/) is
installed: it provisions an isolated environment straight from GitHub.

---

## 1. Install

### uv

```bash
uv tool install icycache
uv tool install "icycache[tray]"   # + icone de tray (Windows)
```

### pip

```bash
pip install icycache
pip install "icycache[tray]"       # + icone de tray (Windows)
```

### npm (Node-only clients)

```bash
npx -y icycache-mcp        # serveur MCP sur stdio
npx -y icycache serve     # la gateway HTTP
```

### From source

```bash
git clone https://github.com/MlkBlk-IT/icycache.git
cd icycache
python -m venv venv
.\venv\Scripts\Activate.ps1        # Linux/macOS: source venv/bin/activate
pip install -e ".[dev]"
venv\Scripts\python tests\run_isolated.py
```

## 2. Build the packages yourself

```bash
pip install build        # or: uv build
python -m build          # writes dist/*.whl and dist/*.tar.gz
```

Artifacts:

- `dist/icycache-<version>-py3-none-any.whl` — the wheel (pure Python, ~300 KB)
- `dist/icycache-<version>.tar.gz` — the sdist (code + configs + dashboard + tests)

The wheel reproduces the repository layout inside the package:

```
icycache/
  src/        <- the modules (flat imports, as in the repo)
  config/     <- the JSON configs
  static/     <- the dashboard and three.js
```

So `PROJECT_ROOT` (the parent of `src/`) points at the right place in both
modes: the repo root when running from a checkout, the installed package
otherwise.

Install a built artifact:

```bash
pip install dist/icycache-0.3.1-py3-none-any.whl
```

## 3. Where the data goes

| Mode | `config/` and `static/` | `db/` and `logs/` |
|---|---|---|
| Checkout source | the repo | the repo (`D:\IcyCache\db`, `D:\IcyCache\logs`) |
| Installed (pip/uvx) | inside the package | `%LOCALAPPDATA%\IcyCache` (Windows) / `~/.local/share/IcyCache` |

The installed package never writes into `site-packages`. Override with:

| Var | Meaning |
|---|---|
| `ICYCACHE_HOME` | Force the data directory (`db/`, `logs/`) |
| `ICYCACHE_DB` | Force the cache database path |
| `ICYCACHE_CONFIG` | Force the gateway config file |
| `ICYCACHE_LOG` | Force the log file |

In the config files, a **relative** path is resolved against the data
directory, an **absolute** path is used as-is.

## 4. Run it

```bash
icycache serve                # the gateway, http://127.0.0.1:8080
icycache serve --port 8090    # on another port
icycache mcp --mode mcp-sse   # the MCP layer (SSE :8081)
icycache mcp --mode mcp-http  # the MCP layer (Streamable HTTP :8084)
icycache doctor               # diagnostic: paths, config, upstreams, database
icycache paths                # where the data lives
icycache version
```

`python -m icycache …` is equivalent.

On Windows, the tray (`tray.py`) starts the gateway (8080) and the MCP layer
(8081 + 8084) together and refuses to start if 8080 is already taken by
another service.

## 5. Wire the MCP server into a client

### Claude Desktop / Cursor / VS Code (stdio)

```json
{
  "mcpServers": {
    "icycache": {
      "command": "npx",
      "args": ["-y", "icycache-mcp"]
    }
  }
}
```

or, with uv:

```json
{
  "mcpServers": {
    "icycache": {
      "command": "uvx",
      "args": ["icycache", "mcp", "--mode", "mcp-stdio"]
    }
  }
}
```

### Hermes (SSE)

```yaml
# %LOCALAPPDATA%\hermes\config.yaml
mcp_servers:
  icycache:
    url: http://127.0.0.1:8081/sse
```

## 6. Verify the connection

```bash
# HTTP gateway
curl http://127.0.0.1:8080/health

# MCP inspector
npx @modelcontextprotocol/inspector npx -y icycache-mcp
```

The MCP layer exposes **11 tools**: `check_tool_loop`, `clear_cache`,
`clear_tool_cache`, `get_cache_stats`, `get_tool_cache_stats`,
`list_proxy_servers`, `list_proxy_tools`, `normalize_preview`, `proxy_tool`,
`proxy_tools`, `search_cached_prompts`.

## 7. Tests

```powershell
# Isolated suite: fake upstream :8191, temp DB, no production port touched
venv\Scripts\python.exe tests\run_isolated.py

# Directly with pytest it does NOT work: test_cacheforge.py requires CF_BASE
# and exits if it is missing. Use the runner above.
```

## 8. Known caveat

`config/icycache.gateway.json` carries **two** `cache` blocks. The code reads
the root one (`max_entries: 50000`, `enable_semantic: false`, 7-day TTL); the
nested `gateway.cache` block (`max_entries: 200000`, `enable_semantic: true`,
0.80 threshold, 30-day TTL) is **never read**. So the semantic cache advertised
in the README is off at runtime. See [`../PROGRESS.md`](../PROGRESS.md) §3.2.
