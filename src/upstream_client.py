"""Client upstream résilient (async) — corrige BUG-001.

Adapté du delta DELTA-002 Volet A en async pour matcher mcp_server.py (httpx.AsyncClient).

Invariants :
  1. Toute réponse (succès OU erreur) est drainée avant retour au pool.
  2. Toute erreur de transport → connexion jetée (recycle) + 1 retry.
  3. Après un 4xx/5xx, l'upstream est marqué "suspect" TTL court ; on sonde
     /v1/models avant de réutiliser le pool.
  4. Le message d'erreur porte status + body (fini le message vide).
"""
from __future__ import annotations
import asyncio
import time
import httpx


class UpstreamError(Exception):
    def __init__(self, upstream: str, status: int, body: str, cause: BaseException | None = None):
        self.upstream = upstream
        self.status = status
        self.body = body[:2000]
        self.cause = cause
        super().__init__(f"{upstream} returned {status}: {self.body[:200]!r} (cause={cause!r})")


class ResilientUpstream:
    """Client async thread-safe-ish (un instance par upstream, usage single-task)."""

    def __init__(self, base_url: str, timeout_s: float = 60.0,
                 connect_s: float = 10.0, suspect_ttl_s: float = 5.0):
        self._base = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_s, connect=connect_s)
        self._client: httpx.AsyncClient | None = None
        self._suspect_until = 0.0
        self._suspect_ttl = suspect_ttl_s
        self._lock = asyncio.Lock()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _recycle(self) -> None:
        if self._client and not self._client.is_closed:
            try:
                await self._client.aclose()
            except Exception:
                pass
        self._client = httpx.AsyncClient(timeout=self._timeout)

    async def _probe(self) -> bool:
        try:
            c = await self._ensure_client()
            r = await c.get(f"{self._base}/v1/models", timeout=5.0)
            await r.aread()
            r.close()
            return True
        except Exception:
            return False

    async def _ensure_healthy(self) -> None:
        if time.monotonic() < self._suspect_until:
            if await self._probe():
                self._suspect_until = 0.0
            else:
                await self._recycle()

    def _mark_suspect(self) -> None:
        self._suspect_until = time.monotonic() + self._suspect_ttl

    async def post_json(self, path: str, json_body: dict, stream: bool = False,
                        headers: dict | None = None) -> httpx.Response:
        async with self._lock:
            await self._ensure_healthy()
            url = f"{self._base}{path}"
            client = await self._ensure_client()
            try:
                resp = await client.post(url, json=json_body, headers=headers or {})
            except (httpx.RemoteProtocolError, httpx.ReadError,
                    httpx.ConnectError, httpx.WriteError) as e:
                await self._recycle()
                client = await self._ensure_client()
                resp = await client.post(url, json=json_body, headers=headers or {})

            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                await resp.aclose()
                self._mark_suspect()
                raise UpstreamError(self._base, resp.status_code, body)

            if not stream:
                await resp.aread()
            return resp

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
