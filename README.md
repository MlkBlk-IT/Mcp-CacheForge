# IcyCache

A local multi-upstream LLM gateway with caching, routing and MCP access.

## Status

Active development. The main gateway is intended to run on port `8080`; the MCP layer exposes stdio and HTTP transports. Verify the current configuration before using it in a shared or production environment.

## What it does

IcyCache gives local applications one entry point for several upstreams. It can:

- route requests to a configured upstream;
- reuse compatible cached results;
- expose the same capabilities through MCP;
- report upstream status and response bodies clearly;
- keep legacy entry points available for isolated testing.

The configured provider list lives in `config/`. Credentials belong in local configuration and must not be committed.

## Quick start

```powershell
git clone https://github.com/MlkBlk-IT/icycache.git
cd icycache
python -m pip install -r requirements.txt
python tray.py
```

Useful entry points:

```powershell
python src/server.py --port 8090
python mcp_stdio.py
```

Use the configuration and verification scripts in the repository to confirm the ports and enabled transports on your machine.

## Transports

- **stdio** — for desktop clients and IDE integrations
- **SSE** — legacy HTTP clients
- **Streamable HTTP** — recent MCP clients and inspectors

The exact endpoint is controlled by the local configuration. Do not assume a remote or public deployment.

## Safety notes

- Cache only data that is safe to retain locally.
- Keep API keys outside Git.
- Treat provider responses and status codes as authoritative.
- A cache hit is not proof that an upstream is currently healthy.

## Author

Built by [Malik Belkacemi](https://github.com/MlkBlk-IT).
