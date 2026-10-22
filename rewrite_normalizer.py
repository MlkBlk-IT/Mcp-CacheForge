# D:\CacheForge\rewrite_normalizer.py
from pathlib import Path

server_path = Path(r"D:\CacheForge\src\server.py")
content = server_path.read_text(encoding="utf-8")

# Backup
backup_path = server_path.with_suffix('.py.bak12')
backup_path.write_text(content, encoding="utf-8")
print(f"✓ Backup créé: {backup_path}")

# Trouver le début et la fin de la classe NormalizationPipeline
lines = content.split('\n')
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if line.startswith('class NormalizationPipeline:'):
        start_idx = i
    elif start_idx is not None and line.startswith('class ') and i > start_idx:
        end_idx = i
        break

if start_idx is None:
    print("✗ Classe NormalizationPipeline introuvable")
    exit(1)

if end_idx is None:
    end_idx = len(lines)

print(f"✓ Classe trouvée: lignes {start_idx+1} à {end_idx}")

# Nouvelle implémentation complète
new_class = '''class NormalizationPipeline:
    """Pipeline de normalisation multi-couches pour maximiser le cache hit rate"""
    
    def __init__(self, rules_path: Path):
        with open(rules_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        # Couche 1: Aho-Corasick pour remplacement multi-patterns
        try:
            import ahocorasick
            self.automaton = ahocorasick.Automaton()
            for rule in self.config['rules']:
                pattern = rule['pattern'].lower()
                replacement = rule['replace']
                self.automaton.add_word(pattern, (pattern, replacement))
            self.automaton.make_automaton()
            print(f"✓ Aho-Corasick chargé: {len(self.config['rules'])} patterns")
        except ImportError:
            print("⚠ ahocorasick non disponible, fallback regex")
            self.automaton = None
            self.regex_compiled = []
            for rule in self.config['rules']:
                pattern = re.compile(r'\\b' + re.escape(rule['pattern'].lower()) + r'\\b', re.IGNORECASE)
                self.regex_compiled.append((pattern, rule['replace']))
        
        # Couche 2: Stemmers Snowball
        try:
            from nltk.stem.snowball import SnowballStemmer
            self.stemmer_fr = SnowballStemmer('french')
            self.stemmer_en = SnowballStemmer('english')
            print("✓ Stemmers NLTK chargés")
        except ImportError:
            print("⚠ nltk non disponible, stemming désactivé")
            self.stemmer_fr = None
            self.stemmer_en = None
        
        # Couche 3: NFKD normalization
        import unicodedata
        self.unicodedata = unicodedata
        
        # Regex pour nettoyage final
        self.cleanup_regex = re.compile(r'[^\\w\\s]', re.UNICODE)
        self.space_regex = re.compile(r'\\s+')
    
    def normalize(self, text: str) -> str:
        """Pipeline complet: NFKD → Lowercase → Aho-Corasick → Strip accents → Stemming → Cleanup"""
        
        # Étape 1: NFKD normalization (normalise les caractères Unicode)
        text = self.unicodedata.normalize('NFKD', text)
        
        # Étape 2: Lowercase
        text = text.lower()
        
        # Étape 3: Aho-Corasick ou Regex pour remplacement multi-patterns
        if self.automaton:
            # Aho-Corasick: remplacement optimal
            matches = list(self.automaton.iter(text))
            # Traiter les matches de droite à gauche pour ne pas invalider les indices
            for end_idx, (pattern, replacement) in reversed(matches):
                start_idx = end_idx - len(pattern) + 1
                text = text[:start_idx] + replacement + text[end_idx + 1:]
        else:
            # Fallback regex
            for pattern, replacement in self.regex_compiled:
                text = pattern.sub(replacement, text)
        
        # Étape 4: Strip accents (garde seulement les caractères de base)
        text = ''.join(
            c for c in self.unicodedata.normalize('NFD', text)
            if self.unicodedata.category(c) != 'Mn'
        )
        
        # Étape 5: Stemming (optionnel, peut être trop agressif)
        # Désactivé pour l'instant car peut casser la sémantique
        # if self.stemmer_fr:
        #     words = text.split()
        #     # Détection simple de langue
        #     if any(word in ['le', 'la', 'les', 'un', 'une', 'des'] for word in words):
        #         text = ' '.join(self.stemmer_fr.stem(w) for w in words)
        #     else:
        #         text = ' '.join(self.stemmer_en.stem(w) for w in words)
        
        # Étape 6: Cleanup final (supprime ponctuation, normalise espaces)
        text = self.cleanup_regex.sub('', text)
        text = self.space_regex.sub(' ', text).strip()
        
        return text
    
    def get_hash(self, normalized_text: str) -> str:
        """Génère le hash SHA256 du texte normalisé"""
        return hashlib.sha256(normalized_text.encode('utf-8')).hexdigest()

'''

# Remplacer la classe
new_lines = lines[:start_idx] + new_class.split('\n') + lines[end_idx:]
new_content = '\n'.join(new_lines)

# Écrire
server_path.write_text(new_content, encoding="utf-8")
print("✓ Classe NormalizationPipeline réécrite avec pipeline multi-couches")

# Vérification syntaxique
import subprocess
result = subprocess.run([r".\venv\Scripts\python.exe", "-m", "py_compile", "src\\server.py"], 
                       capture_output=True, text=True)
if result.returncode == 0:
    print("✓ Syntaxe valide")
else:
    print("✗ Erreur de syntaxe:")
    print(result.stderr)
    exit(1)

print("\n⚠ Redémarre le serveur pour appliquer les changements")
