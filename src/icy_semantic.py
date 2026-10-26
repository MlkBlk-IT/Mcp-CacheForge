"""Cache sémantique hybride — DELTA-002 Volet B.

Qdrant est importé LAZILY: si qdrant-client n'est pas installé ou Qdrant est down,
tout échec = miss silencieux → upstream. Jamais de panne.

Embedding model: auto-discovery via /v1/models (pas de hardcodage).
"""
from __future__ import annotations
import re
import json
import time
import uuid
import threading
import logging

logger = logging.getLogger("icycache.semantic")

# ---- Ancres (grounding anti-faux-positif) -----------------------------------

_HARD = [
    ("num", r"\b\d+(?:[.,]\d+)?\b"),
    ("quote", r"[\"'`]([^\"'`]{1,60})[\"'`]"),
    ("path", r"\b[A-Za-z]:\\[^\s]+|(?:\./|/)[^\s]+"),
    ("unit", r"\b\d+\s?(?:kg|km|cm|mm|mb|gb|tb|ms|min|h|%|°c|°f|usd|eur)\b"),
    ("url", r"https?://[^\s]+"),
]

_NUM_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
              "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
              "ten": "10"}


def normalize_numbers(t: str) -> str:
    """Convertit mots-nombres → chiffres et opérateurs verbaux → symboles.
    À appeler AVANT hash ET avant embedding."""
    for w, d in _NUM_WORDS.items():
        t = re.sub(rf"\b{w}\b", d, t)
    return (t.replace(" plus ", "+").replace(" minus ", "-")
             .replace(" times ", "*").replace(" divided by ", "/"))


def extract_hard(text: str) -> frozenset[str]:
    low = text.lower()
    out = set()
    for _, pat in _HARD:
        out.update(m.group(0).strip() for m in re.finditer(pat, low))
    return frozenset(out)


def extract_soft(text: str) -> frozenset[str]:
    return frozenset(
        m.group(0).lower()
        for m in re.finditer(r"\b[A-Z][a-zà-ÿ]{2,}\b", text)
        if m.start() > 0
    )


def accepts(query_norm: str, cand: dict, score: float,
            HARD_MIN: float = 0.95, RELAX: float = 0.97) -> bool:
    if score < HARD_MIN:
        return False
    qh, ch = extract_hard(query_norm), frozenset(cand.get("anchors_hard", []))
    if qh != ch:
        return False
    qs, cs = extract_soft(query_norm), frozenset(cand.get("anchors_soft", []))
    if qs == cs:
        return True
    # RELAX seulement si ancres hard présentes (grounding numérique/path);
    # sans hard anchors, soft anchors doivent matcher exactement.
    if qh and score >= RELAX:
        return True
    return False


# ---- Embedder (auto-discovery LM Studio) ------------------------------------

class Embedder:
    def __init__(self, base_url: str, model: str | None = None, timeout: float = 10.0):
        import httpx
        self._c = httpx.Client(base_url=base_url, timeout=timeout)
        self._m = model
        if not self._m:
            self._m = self._discover_model()

    def _discover_model(self) -> str:
        try:
            r = self._c.get("/v1/models")
            r.raise_for_status()
            for m in r.json().get("data", []):
                mid = m.get("id", "")
                if "embed" in mid.lower() or "bge" in mid.lower() or "e5" in mid.lower():
                    return mid
            # fallback: premier modèle dispo
            models = r.json().get("data", [])
            if models:
                return models[0]["id"]
        except Exception as e:
            logger.warning("Embedder auto-discovery failed: %s", e)
        raise RuntimeError("No embedding model found via /v1/models")

    def embed(self, text: str) -> list[float]:
        r = self._c.post("/v1/embeddings", json={"model": self._m, "input": text})
        r.raise_for_status()
        v = r.json()["data"][0]["embedding"]
        n = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / n for x in v]


# ---- Store Qdrant (lazy import) ---------------------------------------------

class SemanticStore:
    COLL = "icycache_semantic"
    _qdrant = None
    _client = None

    def __init__(self, url: str):
        self._url = url
        self._lock = threading.Lock()
        self._init_qdrant()

    def _init_qdrant(self):
        try:
            from qdrant_client import QdrantClient, models
            SemanticStore._qdrant = (QdrantClient, models)
            self._client = QdrantClient(url=self._url)
        except ImportError:
            logger.info("qdrant-client not installed — semantic cache disabled (miss silencieux)")
            self._client = None
        except Exception as e:
            logger.warning("Qdrant unreachable: %s — semantic cache disabled", e)
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    def ensure(self, dim: int):
        if not self._client:
            return
        try:
            QdrantClient, models = SemanticStore._qdrant
            if not self._client.collection_exists(self.COLL):
                self._client.create_collection(
                    self.COLL,
                    vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
                )
        except Exception as e:
            logger.warning("Qdrant ensure collection failed: %s", e)
            self._client = None

    def search(self, sig: str, vec: list[float], seuil: float, top: int = 1):
        if not self._client:
            return None
        try:
            QdrantClient, models = SemanticStore._qdrant
            res = self._client.query_points(
                collection_name=self.COLL, query=vec, limit=top,
                score_threshold=seuil,
                query_filter=models.Filter(must=[models.FieldCondition(
                    key="sig", match=models.MatchValue(value=sig))]),
                with_payload=True,
            ).points
            return res[0] if res else None
        except Exception as e:
            logger.warning("Qdrant search failed: %s — miss silencieux", e)
            return None

    def upsert(self, vec: list[float], payload: dict):
        if not self._client:
            return
        try:
            QdrantClient, models = SemanticStore._qdrant
            with self._lock:
                self._client.upsert(self.COLL, points=[models.PointStruct(
                    id=str(uuid.uuid4()), vector=vec, payload=payload)])
        except Exception as e:
            logger.warning("Qdrant upsert failed: %s — index skipped", e)


# ---- Indexation async (ne bloque jamais) ------------------------------------

def index_async(store: SemanticStore, embedder: Embedder, sig: str,
                 query_norm: str, resp_json: str):
    def _job():
        try:
            v = embedder.embed(query_norm)
            store.ensure(len(v))
            store.upsert(v, {
                "sig": sig,
                "response": resp_json,
                "anchors_hard": sorted(extract_hard(query_norm)),
                "anchors_soft": sorted(extract_soft(query_norm)),
                "ts": time.time(),
            })
        except Exception:
            pass  # index échoue → pas grave
    threading.Thread(target=_job, daemon=True).start()
