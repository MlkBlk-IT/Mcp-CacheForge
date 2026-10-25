# D:\CacheForge\update_pipeline.py
import re
from pathlib import Path

# === NOUVELLE CLASSE NormalizationPipeline ===
NEW_CLASS = '''# === CACHEFORGE:START NormalizationPipeline ===
import json
import hashlib
import unicodedata
import re
from pathlib import Path
import ahocorasick
from nltk.stem.snowball import SnowballStemmer

class NormalizationPipeline:
    def __init__(self, rules_path: Path):
        # Charger règles
        data = json.loads(rules_path.read_text(encoding="utf-8"))
        self._rules = data["rules"]
        self._intents = data["intents"]
        self._slots = data["slots"]
        
        # Build Aho-Corasick automaton
        self._automaton = ahocorasick.Automaton()
        for idx, rule in enumerate(self._rules):
            pattern = rule["pattern"].lower()
            replacement = rule["replace"]
            self._automaton.add_word(pattern, (len(pattern), pattern, replacement))
        self._automaton.make_automaton()
        
        # Stemmers
        self._stemmer_fr = SnowballStemmer("french")
        self._stemmer_en = SnowballStemmer("english")
        
        # Suffixes pour lemmatization maison
        self._suffixes_fr = {
            "ment": "", "tion": "", "sion": "", "eur": "", "euse": "",
            "eux": "", "elle": "", "el": "", "ive": "", "if": ""
        }
        self._suffixes_en = {
            "tion": "", "sion": "", "ment": "", "ness": "", "able": "",
            "ible": "", "ous": "", "ive": "", "ing": "", "ed": "", "s": ""
        }
        
        # Regex pré-compilées
        self._space_re = re.compile(r"\\s+")
        self._punct_re = re.compile(r"[^\\w\\s]", re.UNICODE)
        self._intent_res = [(re.compile(i["pattern"], re.I), i["name"]) for i in self._intents]
        self._slot_res = {}
        for intent, slots in self._slots.items():
            self._slot_res[intent] = [(re.compile(s["pattern"], re.I), s["slot"], s["value"]) for s in slots]
    
    def _strip_accents(self, text: str) -> str:
        """NFKD + strip accents (Mn = Mark, Nonspacing)"""
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(c for c in nfkd if not unicodedata.combining(c))
    
    def _lemmatize_home(self, text: str, lang: str = "fr") -> str:
        """Lemmatization maison par suffixes"""
        suffixes = self._suffixes_fr if lang == "fr" else self._suffixes_en
        words = text.split()
        lemmas = []
        for word in words:
            lemmatized = False
            for suffix in sorted(suffixes.keys(), key=len, reverse=True):
                if word.endswith(suffix) and len(word) > len(suffix) + 2:
                    lemmas.append(word[:-len(suffix)] + suffixes[suffix])
                    lemmatized = True
                    break
            if not lemmatized:
                lemmas.append(word)
        return " ".join(lemmas)
    
    def _stem(self, text: str, lang: str = "fr") -> str:
        """Snowball stemming fallback"""
        stemmer = self._stemmer_fr if lang == "fr" else self._stemmer_en
        words = text.split()
        return " ".join(stemmer.stem(w) for w in words)
    
    def _apply_aho(self, text: str) -> str:
        """Aho-Corasick multi-pattern replacement (longest-match)"""
        result = []
        last_end = 0
        for end_idx, (length, pattern, replacement) in self._automaton.iter(text):
            start_idx = end_idx - length + 1
            if start_idx >= last_end:
                result.append(text[last_idx:start_idx])
                result.append(replacement)
                last_end = end_idx + 1
        result.append(text[last_end:])
        return "".join(result)
    
    def _detect_lang(self, text: str) -> str:
        """Détection langue simple (FR si accents ou mots FR communs)"""
        fr_markers = ["le", "la", "les", "un", "une", "des", "de", "du", "et", "est", "sont", "avoir", "être"]
        words = set(text.lower().split())
        fr_count = sum(1 for m in fr_markers if m in words)
        return "fr" if fr_count >= 2 else "en"
    
    def _extract_intent_slots(self, text: str) -> tuple:
        """Extract intent + slots"""
        intent = None
        for pattern, name in self._intent_res:
            if pattern.search(text):
                intent = name
                break
        
        slots = {}
        if intent and intent in self._slot_res:
            for pattern, slot, value in self._slot_res[intent]:
                if pattern.search(text):
                    slots[slot] = value
        
        return intent, slots
    
    def normalize(self, text: str) -> str:
        """Pipeline 7 couches"""
        # [1] NFKD fold
        t = unicodedata.normalize("NFKD", text)
        
        # [2] Strip accents
        t = self._strip_accents(t)
        
        # [3] Lowercase + whitespace canonical
        t = t.lower()
        t = self._punct_re.sub(" ", t)
        t = self._space_re.sub(" ", t).strip()
        
        # [4] Aho-Corasick (1114+ patterns)
        t = self._apply_aho(t)
        
        # [5] Lemmatization maison
        lang = self._detect_lang(t)
        t = self._lemmatize_home(t, lang)
        
        # [6] Snowball stemming fallback
        t = self._stem(t, lang)
        
        # [7] Intent + slots
        intent, slots = self._extract_intent_slots(t)
        
        # [8] Canonical JSON + SHA256
        canonical = {"intent": intent, "slots": slots, "text": t}
        canonical_json = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
# === CACHEFORGE:END NormalizationPipeline ===
'''

# === LECTURE + REMPLACEMENT ===
server_path = Path(r"D:\CacheForge\src\server.py")
content = server_path.read_text(encoding="utf-8")

# Backup
backup_path = server_path.with_suffix(".py.bak")
backup_path.write_text(content, encoding="utf-8")
print(f"✓ Backup: {backup_path}")

# Marqueurs
start_marker = "# === CACHEFORGE:START NormalizationPipeline ==="
end_marker = "# === CACHEFORGE:END NormalizationPipeline ==="

if start_marker not in content:
    # Ajouter marqueurs à la fin si absents
    content += f"\n{start_marker}\n# (ancienne classe ici)\n{end_marker}\n"
    print("⚠ Marqueurs absents, ajoutés à la fin")

# Remplacer
pattern = re.compile(
    re.escape(start_marker) + r".*?" + re.escape(end_marker),
    re.DOTALL
)
new_content = pattern.sub(NEW_CLASS.strip(), content)

# Écrire
server_path.write_text(new_content, encoding="utf-8")
print(f"✓ Pipeline réécrit: {server_path}")
print(f"✓ Taille: {server_path.stat().st_size / 1024:.1f} KB")
