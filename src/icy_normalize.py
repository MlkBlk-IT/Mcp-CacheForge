#!/usr/bin/env python3
"""IcyCache - moteur de normalisation autonome.

Port fidele de `server.NormalizationPipeline`, mais SANS effet de bord a l'import :
ni logging.basicConfig, ni DB, ni app FastAPI, ni client httpx.
Utilisable donc depuis la gateway (server_new.py) et les tests sans perturber
le serveur en cours.

Objectif : la cle de cache porte sur le payload NORMALISE, pas sur le payload
brut. "Bonjour !" et "bonjour" doivent tomber dans la meme entree.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional

__all__ = [
    "Normalizer",
    "normalize_payload",
    "cache_key",
    "canonical",
    "text_blocks",
    "get_default_normalizer",
]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES_PATH = PROJECT_ROOT / "config" / "normalization_rules.json"

# Champs sans effet sur la sortie du modele : exclus de la cle de cache.
CACHE_KEY_IGNORED = {"stream", "stream_options", "user", "metadata"}

# Bloc volatil injecte par Hermes en fin de message ("Retrieved skills (top-K relevant ...)").
# Il change a chaque requete et empeche tout HIT : retire de la CLE uniquement.
_VOLATILE_TAIL = re.compile(r"retrieved skills\W*top\W?k relevant to your query.*$", re.DOTALL)

# Metadata de session injectee par Hermes dans le system prompt
# ("Conversation started: Sunday, November 01, 2026 (America/Montreal, EST, UTC-05:00)").
# Change a chaque session -> retire de la CLE de cache (system prompt uniquement,
# JAMAIS dans les messages utilisateur qui peuvent contenir des dates legitimes).
_VOLATILE_SYSTEM_DATES = re.compile(
    r"Conversation started:.*?(?:UTC[+-]\d{2}:\d{2})\)?",
    re.DOTALL | re.IGNORECASE,
)


def canonical(obj: Any) -> str:
    """JSON canonique (cles triees, separateurs compacts) -> hachage stable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


class Normalizer:
    """Pipeline : NFKD -> lowercase -> Aho-Corasick (politesse/synonymes) -> accents -> ponctuation."""

    def __init__(self, rules_path: Path = DEFAULT_RULES_PATH):
        self.rules_path = Path(rules_path)
        self.config: Dict[str, Any] = {}
        self.automaton = None
        self.regex_compiled = []
        self.unicodedata = unicodedata
        # Ne supprime que la ponctuation de phrase. +-*/=<>%^&|()[]{} etc. sont conserves :
        # "12+2" et "1+22" ne doivent PAS partager la meme cle de cache.
        self.cleanup_regex = re.compile(r"[!?.,;:'\"`\u00b4\u201c\u201d\u2018\u2019\u00ab\u00bb\u00a1\u00bf\u2026]", re.UNICODE)
        self.space_regex = re.compile(r"\s+")

        if self.rules_path.exists():
            with open(self.rules_path, "r", encoding="utf-8") as f:
                self.config = json.load(f)
        else:  # degrade proprement plutot que de planter
            self.config = {"rules": []}

        try:
            import ahocorasick
            self.automaton = ahocorasick.Automaton()
            for rule in self.config.get("rules", []):
                pattern = rule["pattern"].lower()
                self.automaton.add_word(pattern, (pattern, rule["replace"]))
            self.automaton.make_automaton()
        except Exception:
            self.automaton = None
            for rule in self.config.get("rules", []):
                self.regex_compiled.append(
                    (re.compile(r"\b" + re.escape(rule["pattern"].lower()) + r"\b", re.IGNORECASE), rule["replace"])
                )

    # -- normalisation de texte brut -------------------------------------
    def normalize(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return ""
        text = self.unicodedata.normalize("NFKD", text)
        text = text.lower()
        text = self._replace_rules(text)
        # DELTA-002: normalisation des nombres ("two plus two" → "2+2")
        text = self._normalize_numbers(text)
        text = "".join(
            c for c in self.unicodedata.normalize("NFD", text) if self.unicodedata.category(c) != "Mn"
        )
        text = self.cleanup_regex.sub("", text)
        text = self.space_regex.sub(" ", text).strip()
        return _VOLATILE_TAIL.sub("", text).strip()

    def _replace_rules(self, text: str) -> str:
        if self.automaton:
            def _isw(c: str) -> bool:
                return c.isalnum() or c == "_" or self.unicodedata.category(c) == "Mn"
            cands = []
            for end_idx, (pattern, replacement) in self.automaton.iter(text):
                s, e = end_idx - len(pattern) + 1, end_idx + 1
                if s > 0 and _isw(pattern[0]) and _isw(text[s - 1]):
                    continue
                if e < len(text) and _isw(pattern[-1]) and _isw(text[e]):
                    continue
                cands.append((s, e, replacement))
            cands.sort(key=lambda t: (t[0], -(t[1] - t[0])))
            out, pos = [], 0
            for s, e, rep in cands:
                if s < pos:
                    continue
                out.append(text[pos:s])
                out.append(rep)
                pos = e
            out.append(text[pos:])
            return "".join(out)
        for pattern, replacement in self.regex_compiled:
            text = pattern.sub(replacement, text)
        return text

    def _normalize_numbers(self, text: str) -> str:
        """DELTA-002: convertit mots-nombres → chiffres et opérateurs verbaux → symboles."""
        _NUM_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
                      "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
                      "ten": "10"}
        for w, d in _NUM_WORDS.items():
            text = re.sub(rf"\b{w}\b", d, text)
        return (text.replace(" plus ", "+").replace(" minus ", "-")
                 .replace(" times ", "*").replace(" divided by ", "/"))

    def get_hash(self, normalized_text: str) -> str:
        return hashlib.sha256((normalized_text or "").encode("utf-8")).hexdigest()


_DEFAULT: Optional[Normalizer] = None


def get_default_normalizer(rules_path: Path = DEFAULT_RULES_PATH) -> Normalizer:
    """Instance partagee (construction lazy, thread-safe par GIL sur l'assignation)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Normalizer(rules_path)
    return _DEFAULT


def text_blocks(content: Any) -> list:
    """Normalise le contenu textuel d'un message ; les blocs non textuels sont intacts.

    - str  -> texte normalise
    - list -> chaque bloc {"type": "text", "text": ...} est normalise, les autres
      (image_url, input_audio, tool_use...) sont conserves tels quels.
    """
    if isinstance(content, str):
        return get_default_normalizer().normalize(content)
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                b = dict(block)
                b["text"] = get_default_normalizer().normalize(block["text"])
                out.append(b)
            else:
                out.append(block)
        return out
    return content


def strip_system_dates(text: Any) -> Any:
    """Retire les metadata de session volatiles (Conversation started: ...) du system prompt.

    Applique UNIQUEMENT sur le champ 'system' — jamais sur les messages utilisateur.
    """
    if isinstance(text, str):
        return _VOLATILE_SYSTEM_DATES.sub("", text).strip()
    if isinstance(text, list):
        out = []
        for block in text:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                b = dict(block)
                b["text"] = _VOLATILE_SYSTEM_DATES.sub("", b["text"]).strip()
                out.append(b)
            else:
                out.append(block)
        return out
    return text


def normalize_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """Copie du body ou les contenus textuels sont normalises (pour la cle de cache).

    Le body envoye a l'upstream n'est PAS modifie : seule la forme canonique
    servant de cle de cache est normalisee.
    """
    if not isinstance(body, dict):
        return {}
    out: Dict[str, Any] = {k: v for k, v in body.items() if k not in CACHE_KEY_IGNORED}
    for key in ("messages", "system", "prompt", "input"):
        if key in out:
            val = out[key]
            if key == "messages" and isinstance(val, list):
                norm_msgs = []
                for m in val:
                    if not isinstance(m, dict):
                        norm_msgs.append(m)
                        continue
                    nm = {**m, "content": text_blocks(m.get("content"))}
                    nm.pop("tool_call_id", None)  # correlation ID, pas du contenu
                    if isinstance(nm.get("tool_calls"), list):
                        nm["tool_calls"] = [
                            {**tc, "id": ""} if isinstance(tc, dict) else tc
                            for tc in nm["tool_calls"]
                        ]
                    norm_msgs.append(nm)
                out[key] = norm_msgs
            elif key == "system":
                out[key] = text_blocks(strip_system_dates(val))
            else:
                out[key] = text_blocks(val)
    return out


def cache_key(body: Dict[str, Any], scope: Optional[Dict[str, Any]] = None) -> str:
    """SHA256 du payload normalise (+ perimetre optionnel : upstream, type d'endpoint)."""
    payload = normalize_payload(body or {})
    if scope:
        payload = {"__scope": scope, "body": payload}
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()
