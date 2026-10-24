#!/usr/bin/env python3
"""IcyCache upstream helpers.

Extraits de prefix_proxy.py SANS effets de bord a l'import (pas de logging.basicConfig,
pas d'init DB, pas de client httpx). Ne stocke aucune cle API.
"""
import os
import json
import hashlib


def _flag(name, default="1"):
    return os.environ.get(name, default) == "1"


INJECT_ANTHROPIC = _flag("PREFIX_INJECT_ANTHROPIC")      # ajoute cache_control racine
INJECT_OPENAI_KEY = _flag("PREFIX_INJECT_OPENAI_KEY")    # ajoute prompt_cache_key (upstream "openai" seulement)

# Headers client transmis tels quels a l'upstream (aucune cle stockee cote IcyCache).
FWD_HEADERS = {"authorization", "x-api-key", "anthropic-version", "anthropic-beta", "content-type",
               "openai-organization", "openai-project", "http-referer", "x-title", "user-agent"}


def usage_from(kind, u):
    """Normalise le champ usage upstream. Valeurs absentes = None (jamais estimees)."""
    u = u or {}
    if kind == "anthropic":
        i = u.get("input_tokens")
        r = u.get("cache_read_input_tokens") or 0
        w = u.get("cache_creation_input_tokens") or 0
        total_in = (i or 0) + r + w if (i is not None or r or w) else None
        return {"prompt_tokens": total_in, "completion_tokens": u.get("output_tokens"),
                "cached_tokens": r, "cache_write_tokens": w}
    d = u.get("prompt_tokens_details") or {}
    return {"prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
            "cached_tokens": d.get("cached_tokens") or 0, "cache_write_tokens": d.get("cache_write_tokens") or 0}


def mutate(kind, upstream, body):
    """Active le cache de prefixe cote fournisseur. Retourne le nom du champ injecte (ou None)."""
    if kind == "anthropic":
        if INJECT_ANTHROPIC and "cache_control" not in json.dumps(body):
            body["cache_control"] = {"type": "ephemeral"}
            return "cache_control"
    elif upstream == "openai":
        if INJECT_OPENAI_KEY and "prompt_cache_key" not in body:
            msgs = body.get("messages") or []
            sys_txt = next((m.get("content") for m in msgs if m.get("role") in ("system", "developer")), None)
            if sys_txt:
                digest = hashlib.sha256(json.dumps(sys_txt, sort_keys=True).encode("utf-8")).hexdigest()[:16]
                body["prompt_cache_key"] = "ic-" + digest
                return "prompt_cache_key"
    return None


class StreamMeter:
    """Lit les evenements SSE au passage (sans les modifier) pour extraire usage + modele.

    `events` = nombre d'evenements data recus (compteur de CHUNKS, pas de tokens).
    """

    def __init__(self, kind):
        self.kind, self.buf, self.usage, self.model, self.events = kind, b"", {}, None, 0

    def feed(self, chunk):
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if raw == b"[DONE]":
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            self.events += 1
            if self.kind == "anthropic":
                t = ev.get("type")
                if t == "message_start":
                    m = ev.get("message") or {}
                    self.model = m.get("model") or self.model
                    self.usage.update({k: v for k, v in (m.get("usage") or {}).items() if v is not None})
                elif t == "message_delta":
                    self.usage.update({k: v for k, v in (ev.get("usage") or {}).items() if v is not None})
            else:
                self.model = ev.get("model") or self.model
                if ev.get("usage"):
                    self.usage = ev["usage"]
