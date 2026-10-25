#!/usr/bin/env python3
"""IcyCache - proxy d'outils MCP avec cache + garde anti-boucle.

Routage:  Hermes -> IcyCache (proxy_tool) -> serveur MCP cible (stdio) -> resultat
Cache:    table tool_cache (tc_get/tc_set). Cle = sha256(server, tool, args) -> reponses
          re-utilisables telles quelles. Aucune estimation de tokens: seule la latence
          economisee est mesuree (agent_events.saved_latency_ms).
Garde:    ToolLoopGuard - bloque un agent qui rappelle le meme outil avec les memes args
          ET le meme resultat (boucle infinie). Detecte aussi dans l'historique LLM
          (llm_loop_check).
Metriques: agent_events (tool_call / tool_hit / tool_error / tool_blocked).

Points de robustesse (verifies en production) :
  - Sessions persistantes par serveur : un appel d'outil ne respawn plus un processus
    a chaque fois (20s+ de latence observee avec npx/uvx).
  - Les erreurs MCP (isError=true) ne sont JAMAIS mises en cache et renvoient ok=false.
  - Les outils d'ecriture (create_/write_/delete_/...) ne sont pas mis en cache par
    defaut : une reponse en cache doit pouvoir etre rejouee sans effet de bord.
  - Les commandes Windows (.cmd comme npx) sont resolues : CreateProcess ne lance
    pas un script .cmd/.ps1 sans shell.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("IcyCache.tool_proxy")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_config_file() -> Path:
    """Fichier de config du proxy d'outils.

    Le proxy d'outils a SON propre fichier (config/icycache.json). Il ne doit pas
    suivre aveuglement ICYCACHE_CONFIG: mcp_gateway.py positionne cette variable
    sur icycache.gateway.json (qui n'a pas de section "tool_proxy"), ce qui
    vidait silencieusement la liste des serveurs routables.
    """
    explicit = os.environ.get("ICYCACHE_TOOLPROXY_CONFIG")
    if explicit:
        return Path(explicit)
    default = PROJECT_ROOT / "config" / "icycache.json"
    env_cfg = os.environ.get("ICYCACHE_CONFIG")
    if env_cfg:
        p = Path(env_cfg)
        try:
            if "tool_proxy" in json.loads(p.read_text(encoding="utf-8")):
                return p
        except Exception:
            pass
    return default


CONFIG_FILE = _resolve_config_file()


def _load_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        logger.exception("tool_proxy: impossible de lire %s", CONFIG_FILE)
        return {}


CONFIG = _load_config()
TOOL_CACHE_CFG: Dict[str, Any] = CONFIG.get("tool_cache") or {}
TOOL_PROXY_CFG: Dict[str, Any] = CONFIG.get("tool_proxy") or {}
TOOL_SERVERS: Dict[str, Dict[str, Any]] = TOOL_PROXY_CFG.get("servers") or {}
DEFAULT_TTL = int(TOOL_CACHE_CFG.get("ttl_seconds", 3600))
CACHE_ENABLED = bool(TOOL_CACHE_CFG.get("enabled", True))

HERMES_CONFIG = Path(os.environ.get("LOCALAPPDATA") or ".") / "hermes" / "config.yaml"

# Heuristique "outil d'ecriture" : un resultat mis en cache doit pouvoir etre rejoue
# sans effet de bord. Un create_/delete_/push_ ne peut pas l'etre.
_WRITE_HINTS = (
    "create", "write", "update", "delete", "remove", "destroy", "drop", "purge",
    "push", "commit", "merge", "send", "post", "put", "patch", "edit", "save",
    "upload", "download", "install", "uninstall", "publish", "apply", "move",
    "rename", "rebuild", "reset", "clear", "flush", "exec", "execute", "run_",
    "add_", "set_", "new_", "insert", "deploy", "build", "start_", "stop_", "kill",
)


def _is_write_tool(tool: str) -> bool:
    t = (tool or "").lower()
    return any(h in t for h in _WRITE_HINTS)


def _resolve_env_value(val: str) -> str:
    """Resolution paresseuse d'un secret vers la config Hermes.

    Format: "@hermes:mcp_servers.<nom>" -> valeur lue dans %LOCALAPPDATA%\\hermes\\config.yaml.
    Si le noeud est un dict avec 'args', on extrait la valeur du premier arg contenant '='.
    Evite d'ecrire un token dans la config IcyCache.
    """
    if not isinstance(val, str) or not val.startswith("@hermes:"):
        return val
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML est requis pour resoudre '@hermes:' (pip install pyyaml). "
            "Sinon, mettez la valeur en clair dans la config IcyCache."
        ) from exc
    path = val[len("@hermes:"):]
    with open(HERMES_CONFIG, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    node: Any = cfg
    for part in path.split("."):
        node = node[part]
    if isinstance(node, dict):
        for a in node.get("args") or []:
            if isinstance(a, str) and "=" in a:
                return a.split("=", 1)[1]
        raise KeyError(f"aucun secret resolvable dans {path}")
    return str(node)


def _env_for(cfg: Dict[str, Any]) -> Optional[Dict[str, str]]:
    raw = cfg.get("env")
    if not raw:
        return None
    return {k: _resolve_env_value(v) for k, v in raw.items()}


# Import apres la lecture de la config: icy_core lit le meme fichier et initialise la DB.
from icy_core import (  # noqa: E402
    tc_get,
    tc_set,
    tc_flush,
    tc_clear,
    tc_stats,
    log_agent_event,
    llm_loop_check,
    ToolLoopGuard,
)

GUARD = ToolLoopGuard(TOOL_CACHE_CFG)

_TOOL_LIST_CACHE: Dict[str, tuple] = {}
_TOOL_LIST_TTL = float(TOOL_PROXY_CFG.get("list_cache_seconds", 30))


# =============================================================================
# RESOLUTION DE COMMANDE (Windows)
# =============================================================================
def _resolve_command(cmd: str) -> str:
    """Resout une commande d'executable de façon compatible CreateProcess.

    Sous Windows, `npx` seul pointe vers le script sans extension (bash) et
    `npx.ps1` n'est pas executable sans shell: CreateProcess echoue avec
    WinError 2. On prefere donc `<cmd>.cmd`, puis `<cmd>.exe`, puis `<cmd>.bat`.
    """
    if not cmd or os.name != "nt":
        return cmd
    if os.path.isabs(cmd) or (os.path.altsep and os.path.altsep in cmd):
        return cmd
    if os.path.splitext(cmd)[1]:
        return cmd
    for ext in (".cmd", ".exe", ".bat", ".com"):
        found = shutil.which(cmd + ext)
        if found:
            return found
    return cmd


def _venv_python() -> str:
    """Interpreteur Python du venv du projet (le seul qui a les dependances)."""
    if os.name == "nt":
        cand = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
    else:
        cand = PROJECT_ROOT / "venv" / "bin" / "python"
    return str(cand) if cand.exists() else sys.executable


def _expand(value: str) -> str:
    """Remplace les jetons de portabilite dans la config.

    {PROJECT_ROOT} -> racine du projet   {PYTHON} -> interpreteur du venv
    Permet de deplacer IcyCache sans editer la config a la main.
    """
    if not isinstance(value, str):
        return value
    return (value
            .replace("{PROJECT_ROOT}", str(PROJECT_ROOT))
            .replace("{PYTHON}", _venv_python()))


def _stdio_params(cfg: Dict[str, Any]):
    from mcp import StdioServerParameters
    return StdioServerParameters(
        command=_resolve_command(_expand(str(cfg["command"]))),
        args=[_expand(a) for a in (cfg.get("args") or [])],
        env=_env_for(cfg),
    )


def _describe_exc(exc: BaseException) -> str:
    """Message d'erreur utile: anyio encapsule la vraie erreur dans un
    ExceptionGroup ('unhandled errors in a TaskGroup'), ce qui masque la cause."""
    parts: List[str] = []

    def walk(e: BaseException, depth: int = 0) -> None:
        if isinstance(e, BaseExceptionGroup):
            for sub in e.exceptions:
                walk(sub, depth + 1)
        else:
            msg = str(e).strip()
            parts.append(f"{type(e).__name__}: {msg}" if msg else type(e).__name__)

    try:
        walk(exc)
    except Exception:
        parts = []
    if not parts:
        parts = [f"{type(exc).__name__}: {exc}"]
    return " | ".join(parts)


# =============================================================================
# SESSION PERSISTANTE PAR SERVEUR
# =============================================================================
class _ServerConn:
    """Session MCP vivante pour un serveur cible.

    `_exec()` ouvrait un processus + un handshake `initialize` a CHAQUE appel
    d'outil (mesure: 21s pour npx, 12s pour uvx qui installe a chaque fois).
    On garde donc une session par serveur, dans son propre thread + event loop,
    et on y planifie les appels via run_coroutine_threadsafe. Le exit du task
    group anyio reste dans la tache qui l'a ouvert, comme l'exige le SDK.
    """

    def __init__(self, name: str, cfg: Dict[str, Any]):
        self.name = name
        self.cfg = cfg
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._lock = threading.Lock()

    # -- demarrage ---------------------------------------------------------
    def start(self, timeout: float = 60.0) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self._session is not None:
                return
            self._ready.clear()
            self._error = None
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="icy-mcp-%s" % self.name)
            self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError(f"demarrage du serveur MCP {self.name!r} trop long (> {timeout:.0f}s)")
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve())
        except BaseException as exc:  # noqa: BLE001 - remonte a start()
            self._error = exc
        finally:
            self._ready.set()
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    async def _serve(self) -> None:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        params = _stdio_params(self.cfg)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                self._session = session
                self._ready.set()
                while self._session is session:
                    await asyncio.sleep(0.25)

    # -- appels ------------------------------------------------------------
    def _submit(self, coro_factory, timeout: float):
        loop = self._loop
        session = self._session
        if loop is None or session is None:
            raise RuntimeError(f"session MCP {self.name!r} non prete")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(session), loop)
        return fut.result(timeout)

    def call_tool(self, tool: str, args: Dict[str, Any], timeout: float = 300.0):
        return self._submit(lambda s: s.call_tool(tool, args or {}), timeout)

    def list_tools(self, timeout: float = 60.0):
        return self._submit(lambda s: s.list_tools(), timeout)

    def alive(self) -> bool:
        return self._session is not None and self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        # Pas de loop.stop() ni de join (stop() est appele depuis l'event loop principal):
        # _serve() sort seul via `while self._session is session` et ferme
        # ClientSession/stdio_client dans la tache qui les a ouverts (contrat anyio).
        self._session = None


_CONNS: Dict[str, _ServerConn] = {}
_CONNS_LOCK = threading.Lock()


def _conn(server: str) -> _ServerConn:
    with _CONNS_LOCK:
        c = _CONNS.get(server)
        if c is None:
            c = _ServerConn(server, TOOL_SERVERS[server])
            _CONNS[server] = c
        return c


def _drop_conn(server: str) -> None:
    with _CONNS_LOCK:
        c = _CONNS.pop(server, None)
    if c is not None:
        c.stop()


# =============================================================================
# API PUBLIQUE
# =============================================================================
def server_names() -> List[str]:
    return sorted(TOOL_SERVERS)


def tool_cache_key(server: str, tool: str, args: Any) -> str:
    """Cle de cache = hash exact de (serveur, outil, arguments). Deterministe."""
    raw = json.dumps([server, tool, args if args is not None else {}],
                     sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _args_hash(args: Any) -> str:
    raw = json.dumps(args if args is not None else {},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _content_to_json(res) -> Any:
    """Resultat MCP -> structure serialisable (json-able)."""
    if res is None:
        return None
    dump = getattr(res, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:
            pass
    out: List[Any] = []
    for blk in getattr(res, "content", []) or []:
        if isinstance(blk, dict):
            out.append(blk)
        else:
            out.append({k: v for k, v in vars(blk).items() if not k.startswith("_")})
    return out


def _is_error_result(payload: Any) -> bool:
    """Detecte un resultat MCP en erreur (isError=true) dans le payload serialise."""
    if isinstance(payload, dict):
        if payload.get("isError") is True:
            return True
        if payload.get("is_error") is True:
            return True
    return False


def _error_text(payload: Any) -> str:
    """Extrait un texte lisible d'un resultat d'erreur MCP."""
    if isinstance(payload, dict):
        chunks: List[str] = []
        for blk in payload.get("content") or []:
            if isinstance(blk, dict) and blk.get("text"):
                chunks.append(str(blk["text"]))
        if chunks:
            return " ".join(chunks)[:800]
        if payload.get("error"):
            return str(payload["error"])[:800]
    return "erreur MCP"


def _cache_decision(cfg: Dict[str, Any], tool: str) -> bool:
    """Le cache d'outils est OPT-IN par serveur, et les outils d'ecriture sont
    exclus par defaut: rejouer une reponse en cache doit etre sans effet de bord.

    Regles (dans l'ordre):
      1. `cache_tools` present (dict) -> ALLOWLIST stricte: seuls les outils
         listes a true sont caches, tout le reste est execute a chaque fois.
      2. `cache: true` -> heuristique anti-ecriture (les outils create_/write_/
         delete_/push_... ne sont pas caches).
      3. sinon -> pas de cache.
    """
    if not CACHE_ENABLED:
        return False
    per = cfg.get("cache_tools")
    if isinstance(per, dict):
        return bool(per.get(tool, False))
    if not cfg.get("cache", False):
        return False
    if _is_write_tool(tool):
        allow = cfg.get("cache_write_tools") or []
        return tool in allow
    return True


async def _exec(server: str, cfg: Dict[str, Any], tool: str, args: Dict[str, Any]):
    """Execute l'outil. Session persistante si possible, processus jetable sinon."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    from mcp.shared.exceptions import McpError

    conn = _conn(server)
    started = False
    try:
        await asyncio.to_thread(conn.start)
        if not conn.alive():
            raise RuntimeError("session persistante indisponible")
        started = True
        res = await asyncio.to_thread(conn.call_tool, tool, args)
        return _content_to_json(res)
    except McpError:
        # Erreur applicative renvoyee par le serveur (outil inconnu, args invalides...):
        # la session est saine. Ni drop ni rejeu (evite double execution + respawn).
        raise
    except Exception as exc:
        if started and isinstance(exc, TimeoutError):
            # Timeout de l'appel: l'outil peut encore tourner cote serveur -> pas de rejeu.
            raise
        # Session cassee ou demarrage impossible -> on retente en jetable.
        _drop_conn(server)
        logger.warning("tool_proxy: session persistante %s KO (%s), repli jetable", server,
                       _describe_exc(exc))
        params = _stdio_params(cfg)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                res = await session.call_tool(tool, args or {})
        return _content_to_json(res)


async def _list_tools(server: str) -> List[str]:
    """Liste des outils exposes par le serveur cible (avec cache memoire TTL)."""
    now = time.time()
    hit = _TOOL_LIST_CACHE.get(server)
    if hit and now - hit[0] < _TOOL_LIST_TTL:
        return hit[1]

    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    conn = _conn(server)
    names: Optional[List[str]] = None
    try:
        await asyncio.to_thread(conn.start)
        if conn.alive():
            listing = await asyncio.to_thread(conn.list_tools)
            names = [t.name for t in (getattr(listing, "tools", None) or [])]
    except Exception as exc:
        logger.warning("tool_proxy: liste persistante %s KO (%s), repli jetable", server,
                       _describe_exc(exc))
        _drop_conn(server)

    if names is None:
        params = _stdio_params(TOOL_SERVERS[server])
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listing = await session.list_tools()
        names = [t.name for t in (getattr(listing, "tools", None) or [])]

    _TOOL_LIST_CACHE[server] = (now, names)
    return names


async def list_server_tools(server: str) -> Dict[str, Any]:
    if server not in TOOL_SERVERS:
        return {"ok": False, "error": f"serveur inconnu: {server!r}", "servers": server_names()}
    try:
        names = await _list_tools(server)
    except Exception as exc:
        return {"ok": False, "error": _describe_exc(exc)}
    return {"ok": True, "server": server, "tools": names, "count": len(names)}


async def call_tool(server: str, tool: str, args: Any = None,
                    client: Optional[str] = None) -> Dict[str, Any]:
    """Appelle un outil MCP via le proxy: cache -> loop guard -> execution -> stockage."""
    args = args if isinstance(args, dict) else {}
    if server not in TOOL_SERVERS:
        return {"ok": False, "error": f"serveur inconnu: {server!r}", "servers": server_names()}

    cfg = TOOL_SERVERS[server]
    t0 = time.perf_counter()
    cacheable = _cache_decision(cfg, tool)
    key = tool_cache_key(server, tool, args)
    ahash = _args_hash(args)

    # --- HIT: reponse reutilisable telle quelle ---
    if cacheable:
        row = tc_get(key)
        if row:
            result_json, latency_ms, _bytes = row
            log_agent_event("tool_hit", server=server, tool=tool, client=client,
                            args_hash=key, latency_ms=0, saved_latency_ms=latency_ms or 0)
            try:
                payload = json.loads(result_json)
            except Exception:
                payload = result_json
            return {"ok": True, "cached": True, "latency_ms": 0,
                    "saved_latency_ms": latency_ms or 0, "result": payload}

    # --- GARDE ANTI-BOUcle: meme tool + memes args deja bloques ---
    # La cle inclut le serveur: deux serveurs peuvent exposer le meme nom d'outil.
    sess = "%s|%s" % (server, client or "default")
    if GUARD.check_before(sess, tool, ahash):
        GUARD.record_blocked(sess, tool, ahash)
        log_agent_event("tool_blocked", server=server, tool=tool, client=client,
                        args_hash=key, detail={"reason": "loop_guard"})
        return {"ok": False,
                "error": "BOUCLE BLOQUEE: meme outil + memes args + memes resultats (ToolLoopGuard)."}

    # --- MISS: execution reelle ---
    try:
        payload = await _exec(server, cfg, tool, args)
    except Exception as exc:
        log_agent_event("tool_error", server=server, tool=tool, client=client,
                        args_hash=key,
                        latency_ms=int((time.perf_counter() - t0) * 1000),
                        detail={"error": _describe_exc(exc)})
        return {"ok": False, "error": _describe_exc(exc)}
    latency_ms = int((time.perf_counter() - t0) * 1000)

    # --- Erreur MCP: jamais de cache, jamais de faux "ok" ---
    if _is_error_result(payload):
        msg = _error_text(payload)
        log_agent_event("tool_error", server=server, tool=tool, client=client,
                        args_hash=key, latency_ms=latency_ms,
                        detail={"error": msg, "source": "mcp_isError"})
        return {"ok": False, "cached": False, "latency_ms": latency_ms,
                "error": msg, "result": payload}

    result_json = json.dumps(payload, ensure_ascii=False, default=str)
    rhash = hashlib.sha256(result_json.encode("utf-8")).hexdigest()[:16]
    level, cycle_len, reps = GUARD.after(sess, tool, ahash, rhash)

    if cacheable:
        ttl = int(cfg.get("ttl_seconds", DEFAULT_TTL))
        try:
            tc_set(key, server, tool, result_json, len(result_json.encode("utf-8")), latency_ms, ttl)
        except Exception:
            logger.exception("tc_set a echoue pour %s/%s", server, tool)

    log_agent_event("tool_call", server=server, tool=tool, client=client,
                    args_hash=key, nbytes=len(result_json.encode("utf-8")),
                    latency_ms=latency_ms, detail={"cached": False, "cacheable": cacheable})

    out: Dict[str, Any] = {"ok": True, "cached": False, "latency_ms": latency_ms, "result": payload}
    if level in ("warn", "block"):
        out["loop_warning"] = {"level": level, "cycle_len": cycle_len, "reps": reps}
    return out


async def batch_call(calls: List[Dict[str, Any]],
                     client: Optional[str] = None) -> List[Dict[str, Any]]:
    """Appel batch: [{'server':..,'tool':..,'args':{..}}, ...]."""
    out: List[Dict[str, Any]] = []
    calls = calls or []
    if calls and all(isinstance(c, dict) for c in calls):
        # Les appels vers des serveurs differents sont independants -> en parallele.
        # Un meme serveur reste serialise (session MCP partagee, pas de concurrence).
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for c in calls:
            groups.setdefault(str(c.get("server")), []).append(c)
        if len(groups) > 1:
            results = await asyncio.gather(
                *[call_tool(c.get("server"), c.get("tool"), c.get("args") or {}, client)
                  for c in calls],
                return_exceptions=True)
            for c, r in zip(calls, results):
                if isinstance(r, BaseException):
                    out.append({"ok": False, "error": _describe_exc(r)})
                else:
                    out.append(r)
            return out

    for call in calls:
        if not isinstance(call, dict):
            out.append({"ok": False, "error": "call invalide (dict attendu)"})
            continue
        out.append(await call_tool(call.get("server"), call.get("tool"),
                                   call.get("args") or {}, client))
    return out


def llm_messages_guard(messages: Any, warn_after: int = 3,
                       max_cycle: int = 3) -> Optional[Dict[str, Any]]:
    """Boucle d'agent detectee dans l'historique LLM (memes tool calls + memes resultats)."""
    return llm_loop_check(messages, warn_after=warn_after, max_cycle=max_cycle)


def clear_cache(server: Optional[str] = None) -> Dict[str, Any]:
    try:
        n = tc_clear(server)
    except Exception as exc:
        return {"ok": False, "error": _describe_exc(exc)}
    return {"ok": True, "cleared": n, "server": server or "all"}


def flush_server(server: str) -> Dict[str, Any]:
    try:
        n = tc_flush(server)
    except Exception as exc:
        return {"ok": False, "error": _describe_exc(exc)}
    return {"ok": True, "cleared": n, "server": server}


def stats() -> Dict[str, Any]:
    try:
        return {"ok": True, **tc_stats()}
    except Exception as exc:
        return {"ok": False, "error": _describe_exc(exc)}


def connection_status() -> Dict[str, Any]:
    """Etat des sessions persistantes (diagnostic)."""
    with _CONNS_LOCK:
        items = list(_CONNS.values())
    return {c.name: {"alive": c.alive(), "error": _describe_exc(c._error) if c._error else None}
            for c in items}
