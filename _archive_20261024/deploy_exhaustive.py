# D:\CacheForge\deploy_exhaustive.py
import json
from pathlib import Path
from datetime import datetime

# === VERBES FR (top 100) ===
VERBES_FR = {
    "être": ["suis", "es", "est", "sommes", "êtes", "sont", "étais", "était", "étions", "étiez", "étaient", "fus", "fut", "fûmes", "fûtes", "furent", "serai", "seras", "sera", "serons", "serez", "seront"],
    "avoir": ["ai", "as", "a", "avons", "avez", "ont", "avais", "avait", "avions", "aviez", "avaient", "eus", "eut", "eûmes", "eûtes", "eurent", "aurai", "auras", "aura", "aurons", "aurez", "auront"],
    "aller": ["vais", "vas", "va", "allons", "allez", "vont", "allais", "allait", "allions", "alliez", "allaient", "allai", "alla", "allâmes", "allâtes", "allèrent", "irai", "iras", "ira", "irons", "irez", "iront"],
    "faire": ["fais", "fait", "faisons", "faites", "font", "faisais", "faisait", "faisions", "faisiez", "faisaient", "fis", "fit", "fîmes", "fîtes", "firent", "ferai", "feras", "fera", "ferons", "ferez", "feront"],
    "dire": ["dis", "dit", "disons", "dites", "disent", "disais", "disait", "disions", "disiez", "disaient", "dis", "dit", "dîmes", "dîtes", "dirent", "dirai", "diras", "dira", "dirons", "direz", "diront"],
    "pouvoir": ["peux", "peut", "pouvons", "pouvez", "peuvent", "pouvais", "pouvait", "pouvions", "pouviez", "pouvaient", "pus", "put", "pûmes", "pûtes", "purent", "pourrai", "pourras", "pourra", "pourrons", "pourrez", "pourront"],
    "vouloir": ["veux", "veut", "voulons", "voulez", "veulent", "voulais", "voulait", "voulions", "vouliez", "voulaient", "voulus", "voulut", "voulûmes", "voulûtes", "voulurent", "voudrai", "voudras", "voudra", "voudrons", "voudrez", "voudront"],
    "voir": ["vois", "voit", "voyons", "voyez", "voient", "voyais", "voyait", "voyions", "voyiez", "voyaient", "vis", "vit", "vîmes", "vîtes", "virent", "verrai", "verras", "verra", "verrons", "verrez", "verront"],
    "savoir": ["sais", "sait", "savons", "savez", "savent", "savais", "savait", "savions", "saviez", "savaient", "sus", "sut", "sûmes", "sûtes", "surent", "saurai", "sauras", "saura", "saurons", "saurez", "sauront"],
    "devoir": ["dois", "doit", "devons", "devez", "doivent", "devais", "devait", "devions", "deviez", "devaient", "dus", "dut", "dûmes", "dûtes", "durent", "devrai", "devras", "devra", "devrons", "devrez", "devront"],
    "prendre": ["prends", "prend", "prenons", "prenez", "prennent", "prenais", "prenait", "prenions", "preniez", "prenaient", "pris", "prit", "prîmes", "prîtes", "prirent", "prendrai", "prendras", "prendra", "prendrons", "prendrez", "prendront"],
    "venir": ["viens", "vient", "venons", "venez", "viennent", "venais", "venait", "venions", "veniez", "venaient", "vins", "vint", "vînmes", "vîntes", "vinrent", "viendrai", "viendras", "viendra", "viendrons", "viendrez", "viendront"],
    "donner": ["donne", "donnes", "donnons", "donnez", "donnent", "donnais", "donnait", "donnions", "donniez", "donnaient", "donnai", "donna", "donnâmes", "donnâtes", "donnèrent", "donnerai", "donneras", "donnera", "donnerons", "donnerez", "donneront"],
    "trouver": ["trouve", "trouves", "trouvons", "trouvez", "trouvent", "trouvais", "trouvait", "trouvions", "trouviez", "trouvaient", "trouvai", "trouva", "trouvâmes", "trouvâtes", "trouvèrent", "trouverai", "trouveras", "trouvera", "trouverons", "trouverez", "trouveront"],
    "parler": ["parle", "parles", "parlons", "parlez", "parlent", "parlais", "parlait", "parlions", "parliez", "parlaient", "parlai", "parla", "parlâmes", "parlâtes", "parlèrent", "parlerai", "parleras", "parlera", "parlerons", "parlerez", "parleront"],
    "mettre": ["mets", "met", "mettons", "mettez", "mettent", "mettais", "mettait", "mettions", "mettiez", "mettaient", "mis", "mit", "mîmes", "mîtes", "mirent", "mettrai", "mettras", "mettra", "mettrons", "mettrez", "mettront"],
    "partir": ["pars", "part", "partons", "partez", "partent", "partais", "partait", "partions", "partiez", "partaient", "partis", "partit", "partîmes", "partîtes", "partirent", "partirai", "partiras", "partira", "partirons", "partirez", "partiront"],
    "voir": ["vois", "voit", "voyons", "voyez", "voient", "voyais", "voyait", "voyions", "voyiez", "voyaient", "vis", "vit", "vîmes", "vîtes", "virent", "verrai", "verras", "verra", "verrons", "verrez", "verront"],
    "devoir": ["dois", "doit", "devons", "devez", "doivent", "devais", "devait", "devions", "deviez", "devaient", "dus", "dut", "dûmes", "dûtes", "durent", "devrai", "devras", "devra", "devrons", "devrez", "devront"],
    "savoir": ["sais", "sait", "savons", "savez", "savent", "savais", "savait", "savions", "saviez", "savaient", "sus", "sut", "sûmes", "sûtes", "surent", "saurai", "sauras", "saura", "saurons", "saurez", "sauront"],
    "pouvoir": ["peux", "peut", "pouvons", "pouvez", "peuvent", "pouvais", "pouvait", "pouvions", "pouviez", "pouvaient", "pus", "put", "pûmes", "pûtes", "purent", "pourrai", "pourras", "pourra", "pourrons", "pourrez", "pourront"]
}

# === VERBES EN (top 100 irréguliers + réguliers) ===
VERBES_EN = {
    "be": ["am", "is", "are", "was", "were", "been", "being"],
    "have": ["has", "had", "having"],
    "do": ["does", "did", "done", "doing"],
    "go": ["goes", "went", "gone", "going"],
    "say": ["says", "said", "saying"],
    "get": ["gets", "got", "gotten", "getting"],
    "make": ["makes", "made", "making"],
    "know": ["knows", "knew", "known", "knowing"],
    "think": ["thinks", "thought", "thinking"],
    "take": ["takes", "took", "taken", "taking"],
    "see": ["sees", "saw", "seen", "seeing"],
    "come": ["comes", "came", "coming"],
    "want": ["wants", "wanted", "wanting"],
    "use": ["uses", "used", "using"],
    "find": ["finds", "found", "finding"],
    "give": ["gives", "gave", "given", "giving"],
    "tell": ["tells", "told", "telling"],
    "work": ["works", "worked", "working"],
    "call": ["calls", "called", "calling"],
    "try": ["tries", "tried", "trying"],
    "ask": ["asks", "asked", "asking"],
    "need": ["needs", "needed", "needing"],
    "feel": ["feels", "felt", "feeling"],
    "become": ["becomes", "became", "becoming"],
    "leave": ["leaves", "left", "leaving"],
    "put": ["puts", "putting"],
    "mean": ["means", "meant", "meaning"],
    "keep": ["keeps", "kept", "keeping"],
    "let": ["lets", "letting"],
    "begin": ["begins", "began", "begun", "beginning"],
    "seem": ["seems", "seemed", "seeming"],
    "help": ["helps", "helped", "helping"],
    "show": ["shows", "showed", "shown", "showing"],
    "hear": ["hears", "heard", "hearing"],
    "play": ["plays", "played", "playing"],
    "run": ["runs", "ran", "running"],
    "move": ["moves", "moved", "moving"],
    "live": ["lives", "lived", "living"],
    "believe": ["believes", "believed", "believing"],
    "bring": ["brings", "brought", "bringing"],
    "happen": ["happens", "happened", "happening"],
    "write": ["writes", "wrote", "written", "writing"],
    "sit": ["sits", "sat", "sitting"],
    "stand": ["stands", "stood", "standing"],
    "lose": ["loses", "lost", "losing"],
    "pay": ["pays", "paid", "paying"],
    "meet": ["meets", "met", "meeting"],
    "include": ["includes", "included", "including"],
    "continue": ["continues", "continued", "continuing"],
    "set": ["sets", "setting"],
    "learn": ["learns", "learned", "learning"],
    "change": ["changes", "changed", "changing"],
    "lead": ["leads", "led", "leading"],
    "understand": ["understands", "understood", "understanding"],
    "watch": ["watches", "watched", "watching"],
    "follow": ["follows", "followed", "following"],
    "stop": ["stops", "stopped", "stopping"],
    "create": ["creates", "created", "creating"],
    "speak": ["speaks", "spoke", "spoken", "speaking"],
    "read": ["reads", "reading"],
    "allow": ["allows", "allowed", "allowing"],
    "add": ["adds", "added", "adding"],
    "spend": ["spends", "spent", "spending"],
    "grow": ["grows", "grew", "grown", "growing"],
    "open": ["opens", "opened", "opening"],
    "walk": ["walks", "walked", "walking"],
    "win": ["wins", "won", "winning"],
    "teach": ["teaches", "taught", "teaching"],
    "offer": ["offers", "offered", "offering"],
    "remember": ["remembers", "remembered", "remembering"],
    "love": ["loves", "loved", "loving"],
    "consider": ["considers", "considered", "considering"],
    "appear": ["appears", "appeared", "appearing"],
    "buy": ["buys", "bought", "buying"],
    "wait": ["waits", "waited", "waiting"],
    "serve": ["serves", "served", "serving"],
    "die": ["dies", "died", "dying"],
    "send": ["sends", "sent", "sending"],
    "expect": ["expects", "expected", "expecting"],
    "build": ["builds", "built", "building"],
    "stay": ["stays", "stayed", "staying"],
    "fall": ["falls", "fell", "fallen", "falling"],
    "cut": ["cuts", "cutting"],
    "reach": ["reaches", "reached", "reaching"],
    "kill": ["kills", "killed", "killing"],
    "remain": ["remains", "remained", "remaining"]
}

# === PLURIELS IRRÉGULIERS ===
PLURIELS_IRR_FR = {
    "chevaux": "cheval", "travaux": "travail", "journaux": "journal",
    "baux": "bail", "coraux": "coral", "émaux": "émail",
    "soupiraux": "soupirail", "vantaux": "vantail", "vitriaux": "vitrail",
    "yeux": "oeil", "cieux": "ciel"
}

PLURIELS_IRR_EN = {
    "children": "child", "mice": "mouse", "teeth": "tooth",
    "feet": "foot", "men": "man", "women": "woman",
    "geese": "goose", "oxen": "ox", "lice": "louse",
    "people": "person", "sheep": "sheep", "fish": "fish",
    "deer": "deer", "moose": "moose", "series": "series",
    "species": "species", "aircraft": "aircraft", "craft": "craft"
}

# === FAUTES DE FRAPPE ===
TYPOS_FR = {
    "apartement": "appartement", "aceuil": "accueil", "developpement": "développement",
    "evenement": "événement", "probleme": "problème", "reponse": "réponse",
    "systeme": "système", "methode": "méthode", "categorie": "catégorie",
    "securite": "sécurité", "reseau": "réseau", "acces": "accès",
    "ecriture": "écriture", "execution": "exécution", "creation": "création",
    "deplacement": "déplacement", "telecharger": "télécharger", "deconnexion": "déconnexion",
    "parametre": "paramètre", "propriete": "propriété", "configurer": "configurer",
    "installer": "installer", "desinstaller": "désinstaller", "redemarrer": "redémarrer",
    "eteindre": "éteindre", "deverrouiller": "déverrouiller", "reinitialiser": "réinitialiser",
    "demonter": "démonter", "deployer": "déployer", "reparer": "réparer",
    "defragmenter": "défragmenter", "dechiffrer": "déchiffrer", "debloquer": "débloquer"
}

TYPOS_EN = {
    "recieve": "receive", "occured": "occurred", "seperate": "separate",
    "definately": "definitely", "accomodate": "accommodate", "acheive": "achieve",
    "accross": "across", "agressive": "aggressive", "alot": "a lot",
    "apparantly": "apparently", "arguement": "argument", "basicly": "basically",
    "begining": "beginning", "beleive": "believe", "buisness": "business",
    "calender": "calendar", "catagory": "category", "comming": "coming",
    "commitee": "committee", "completly": "completely", "concious": "conscious",
    "decieve": "deceive", "desparate": "desperate", "diffrent": "different",
    "disapear": "disappear", "disapoint": "disappoint", "embarass": "embarrass",
    "enviroment": "environment", "exagerate": "exaggerate", "excercise": "exercise",
    "existance": "existence", "experiance": "experience", "familar": "familiar",
    "finaly": "finally", "foriegn": "foreign", "fourty": "forty",
    "freind": "friend", "goverment": "government", "grammer": "grammar",
    "happend": "happened", "heighth": "height", "heros": "heroes",
    "immediatly": "immediately", "independant": "independent", "intresting": "interesting",
    "knowlege": "knowledge", "libary": "library", "maintainance": "maintenance",
    "managment": "management", "neccessary": "necessary", "noticable": "noticeable",
    "occurence": "occurrence", "offical": "official", "oportunity": "opportunity",
    "peice": "piece", "percieve": "perceive", "performence": "performance",
    "personell": "personnel", "posession": "possession", "preceed": "precede",
    "privelege": "privilege", "promiss": "promise", "publically": "publicly",
    "realy": "really", "reciept": "receipt", "reccomend": "recommend",
    "refered": "referred", "relevent": "relevant", "remeber": "remember",
    "resistence": "resistance", "responsability": "responsibility", "rythm": "rhythm",
    "saftey": "safety", "scedule": "schedule", "seize": "seize",
    "similer": "similar", "sincerly": "sincerely", "strenght": "strength",
    "succesful": "successful", "supercede": "supersede", "suprise": "surprise",
    "temperture": "temperature", "tendancy": "tendency", "therefor": "therefore",
    "tommorrow": "tomorrow", "truely": "truly", "unforseen": "unforeseen",
    "unfortunatly": "unfortunately", "untill": "until", "useable": "usable",
    "vaccuum": "vacuum", "vehical": "vehicle", "wether": "weather",
    "wich": "which", "writting": "writing"
}

# === SYNONYMES TECH ===
TECH_SYNONYMES = {
    "cloud": "cloud", "nuage": "cloud", "aws": "amazon web services", "azure": "microsoft azure",
    "gcp": "google cloud platform", "iaas": "infrastructure as a service",
    "paas": "platform as a service", "saas": "software as a service",
    "vm": "virtual machine", "vms": "virtual machines", "instance": "instance",
    "container": "container", "containers": "containers", "pod": "pod",
    "kubernetes": "kubernetes", "k8s": "kubernetes", "docker": "docker",
    "network": "network", "reseau": "network", "ip": "ip address",
    "subnet": "subnet", "vlan": "vlan", "vpn": "virtual private network",
    "dns": "domain name system", "dhcp": "dynamic host configuration protocol",
    "http": "hypertext transfer protocol", "https": "hypertext transfer protocol secure",
    "ssh": "secure shell", "ftp": "file transfer protocol", "sftp": "secure file transfer protocol",
    "tcp": "transmission control protocol", "udp": "user datagram protocol",
    "firewall": "firewall", "pare-feu": "firewall", "router": "router",
    "routeur": "router", "switch": "switch", "commutateur": "switch",
    "security": "security", "securite": "security", "cybersecurity": "cybersecurity",
    "cybersecurite": "cybersecurity", "encryption": "encryption", "chiffrement": "encryption",
    "ids": "intrusion detection system", "ips": "intrusion prevention system",
    "siem": "security information and event management", "soc": "security operations center",
    "pentest": "penetration test", "vulnerability": "vulnerability", "vulnerabilite": "vulnerability",
    "exploit": "exploit", "malware": "malware", "ransomware": "ransomware",
    "phishing": "phishing", "hameconnage": "phishing",
    "code": "code", "programming": "programming", "developpement": "development",
    "api": "application programming interface", "rest": "representational state transfer",
    "json": "javascript object notation", "xml": "extensible markup language",
    "html": "hypertext markup language", "css": "cascading style sheets",
    "javascript": "javascript", "python": "python", "java": "java",
    "git": "git", "github": "github", "gitlab": "gitlab",
    "ci": "continuous integration", "cd": "continuous delivery",
    "cicd": "continuous integration continuous delivery", "devops": "devops",
    "agile": "agile", "scrum": "scrum", "kanban": "kanban",
    "admin": "administrator", "administrateur": "administrator",
    "user": "user", "utilisateur": "user", "account": "account",
    "compte": "account", "password": "password", "mot de passe": "password",
    "permission": "permission", "autorisation": "authorization",
    "group": "group", "groupe": "group", "role": "role",
    "policy": "policy", "politique": "policy", "gpo": "group policy object",
    "ad": "active directory", "ldap": "lightweight directory access protocol",
    "backup": "backup", "sauvegarde": "backup", "restore": "restore",
    "restaurer": "restore", "log": "log", "journal": "log",
    "monitor": "monitor", "surveiller": "monitor", "alert": "alert",
    "alerte": "alert", "ticket": "ticket", "incident": "incident",
    "problem": "problem", "probleme": "problem", "request": "request",
    "demande": "request", "change": "change", "changement": "change",
    "app": "application", "apps": "applications", "config": "configuration",
    "configs": "configurations", "db": "database", "dbs": "databases",
    "env": "environment", "envs": "environments", "prod": "production",
    "dev": "development", "staging": "staging", "test": "testing",
    "qa": "quality assurance", "ui": "user interface", "ux": "user experience",
    "os": "operating system", "hw": "hardware", "sw": "software",
    "fw": "firmware", "bios": "basic input output system",
    "uefi": "unified extensible firmware interface", "cpu": "central processing unit",
    "gpu": "graphics processing unit", "ram": "random access memory",
    "ssd": "solid state drive", "hdd": "hard disk drive",
    "nvme": "non-volatile memory express", "usb": "universal serial bus",
    "hdmi": "high-definition multimedia interface", "vga": "video graphics array",
    "lan": "local area network", "wan": "wide area network",
    "wlan": "wireless local area network", "wlan": "wireless local area network",
    "ssid": "service set identifier", "mac": "media access control",
    "nat": "network address translation", "dmz": "demilitarized zone",
    "ssl": "secure sockets layer", "tls": "transport layer security",
    "jwt": "json web token", "oauth": "open authorization",
    "saml": "security assertion markup language", "mfa": "multi-factor authentication",
    "2fa": "two-factor authentication", "otp": "one-time password",
    "rbac": "role-based access control", "acl": "access control list",
    "cve": "common vulnerabilities and exposures", "cvss": "common vulnerability scoring system",
    "owasp": "open web application security project", "xss": "cross-site scripting",
    "csrf": "cross-site request forgery", "sql": "structured query language",
    "nosql": "not only sql", "orm": "object-relational mapping",
    "crud": "create read update delete", "restful": "representational state transfer",
    "graphql": "graph query language", "websocket": "web socket",
    "cdn": "content delivery network", "cdn": "content delivery network",
    "dns": "domain name system", "url": "uniform resource locator",
    "uri": "uniform resource identifier", "urn": "uniform resource name",
    "http2": "hypertext transfer protocol 2", "http3": "hypertext transfer protocol 3",
    "grpc": "google remote procedure call", "protobuf": "protocol buffers",
    "yaml": "yaml ain't markup language", "toml": "tom's obvious minimal language",
    "ini": "initialization", "cfg": "configuration",
    "regex": "regular expression", "regexp": "regular expression",
    "ascii": "american standard code for information interchange",
    "unicode": "unicode", "utf8": "unicode transformation format 8",
    "utf16": "unicode transformation format 16", "utf32": "unicode transformation format 32",
    "base64": "base64 encoding", "hex": "hexadecimal",
    "md5": "message digest 5", "sha1": "secure hash algorithm 1",
    "sha256": "secure hash algorithm 256", "sha512": "secure hash algorithm 512",
    "aes": "advanced encryption standard", "rsa": "rivest shamir adleman",
    "ecc": "elliptic curve cryptography", "pki": "public key infrastructure",
    "ca": "certificate authority", "crl": "certificate revocation list",
    "ocsp": "online certificate status protocol", "csr": "certificate signing request",
    "pem": "privacy-enhanced mail", "der": "distinguished encoding rules",
    "p12": "pkcs12", "pfx": "personal information exchange",
    "jks": "java keystore", "keystore": "keystore",
    "truststore": "truststore", "hsm": "hardware security module",
    "kms": "key management service", "vault": "vault",
    "secret": "secret", "credential": "credential",
    "token": "token", "key": "key", "certificate": "certificate",
    "signature": "signature", "hash": "hash",
    "encrypt": "encrypt", "decrypt": "decrypt",
    "chiffrer": "encrypt", "dechiffrer": "decrypt",
    "signer": "sign", "verifier": "verify",
    "authentifier": "authenticate", "autoriser": "authorize",
    "bloquer": "block", "debloquer": "unblock",
    "suspendre": "suspend", "resumer": "resume",
    "arreter": "stop", "demarrer": "start",
    "redemarrer": "restart", "formater": "format",
    "partitionner": "partition", "monter": "mount",
    "demonter": "unmount", "synchroniser": "synchronize",
    "repliquer": "replicate", "migrer": "migrate",
    "monitorer": "monitor", "surveiller": "monitor",
    "alerter": "alert", "journaliser": "log",
    "auditer": "audit", "analyser": "analyze",
    "diagnostiquer": "diagnose", "optimiser": "optimize",
    "nettoyer": "clean", "compresser": "compress",
    "archiver": "archive", "extraire": "extract",
    "encoder": "encode", "decoder": "decode",
    "copier": "copy", "coller": "paste",
    "couper": "cut", "annuler": "cancel",
    "retablir": "restore", "sauvegarder": "backup",
    "restaurer": "restore", "exporter": "export",
    "importer": "import", "convertir": "convert",
    "imprimer": "print", "scanner": "scan",
    "envoyer": "send", "recevoir": "receive",
    "partager": "share", "telecharger": "download",
    "uploader": "upload", "renommer": "rename",
    "supprimer": "delete", "deplacer": "move",
    "redimensionner": "resize", "pivoter": "rotate",
    "zoomer": "zoom", "defiler": "scroll",
    "naviguer": "navigate", "rechercher": "search",
    "trouver": "find", "filtrer": "filter",
    "trier": "sort", "grouper": "group",
    "selectionner": "select", "deselectionner": "deselect",
    "invertir": "invert", "valider": "validate",
    "confirmer": "confirm", "appliquer": "apply",
    "enregistrer": "save", "fermer": "close",
    "ouvrir": "open", "minimiser": "minimize",
    "maximiser": "maximize", "reduire": "reduce",
    "agrandir": "enlarge", "actualiser": "refresh",
    "rafraichir": "refresh", "recharger": "reload",
    "verrouiller": "lock", "connecter": "connect",
    "deconnecter": "disconnect", "mettre a jour": "update"
}

# === POLITESSE / FILLER ===
POLITESSE_FR = [
    "s'il te plait", "s'il vous plait", "stp", "svp", "merci",
    "est-ce que tu peux", "est-ce que vous pouvez", "j'aimerais que",
    "je voudrais", "pourrais-tu", "pourriez-vous", "peux-tu", "pouvez-vous",
    "s'il te plaît", "s'il vous plaît", "merci beaucoup", "d'avance",
    "je te remercie", "je vous remercie", "en te remerciant", "en vous remerciant"
]

POLITESSE_EN = [
    "please", "thank you", "thanks", "could you", "can you",
    "i would like", "i'd like", "would you mind", "do you think you could",
    "if you don't mind", "i was wondering if", "i was hoping you could",
    "would it be possible to", "is it possible to", "could you please",
    "can you please", "thanks in advance", "thank you in advance",
    "i appreciate", "i'd appreciate", "it would be great if"
]

FILLER_FR = [
    "en fait", "en realite", "basiquement", "simplement", "juste",
    "en gros", "pour faire simple", "bref", "bon", "bien",
    "alors", "donc", "voila", "voilà", "en effet",
    "c'est-à-dire", "c'est a dire", "en quelque sorte", "disons",
    "mettons", "supposons", "imaginons", "par exemple", "comme"
]

FILLER_EN = [
    "basically", "actually", "simply", "just", "essentially",
    "in fact", "really", "honestly", "literally", "practically",
    "so", "well", "anyway", "anyways", "look",
    "listen", "i mean", "you know", "right", "okay",
    "ok", "alright", "i guess", "i suppose", "i think"
]

# === INTENTS ===
INTENTS = [
    {"pattern": "explique|explain|how to|comment faire|qu'est-ce que|what is", "name": "explain"},
    {"pattern": "setup|configure|install|créer|create|build", "name": "configure"},
    {"pattern": "fix|réparer|déboguer|troubleshoot|debug|repair", "name": "troubleshoot"},
    {"pattern": "compare|comparer|difference|vs|versus", "name": "compare"},
    {"pattern": "optimize|optimiser|improve|améliorer|speed up|accélérer", "name": "optimize"},
    {"pattern": "secure|sécuriser|protect|protéger|harden|durcir", "name": "secure"},
    {"pattern": "monitor|surveiller|track|suivre|log|journaliser", "name": "monitor"},
    {"pattern": "backup|sauvegarder|restore|restaurer|migrate|migrer", "name": "backup"},
    {"pattern": "test|tester|validate|valider|verify|vérifier", "name": "test"},
    {"pattern": "deploy|déployer|release|publier|publish", "name": "deploy"}
]

# === SLOTS ===
SLOTS = {
    "explain": [
        {"pattern": "hyperv|hyper-v|hyper v", "slot": "topic", "value": "hyper-v"},
        {"pattern": "vm|virtual machine|machine virtuelle", "slot": "topic", "value": "vm"},
        {"pattern": "docker|container|conteneur", "slot": "topic", "value": "docker"},
        {"pattern": "kubernetes|k8s", "slot": "topic", "value": "kubernetes"},
        {"pattern": "linux|debian|ubuntu|fedora|centos|alma|rocky", "slot": "topic", "value": "linux"},
        {"pattern": "windows|win11|win10|server", "slot": "topic", "value": "windows"},
        {"pattern": "network|reseau|networking", "slot": "topic", "value": "network"},
        {"pattern": "security|securite|cybersecurity", "slot": "topic", "value": "security"},
        {"pattern": "cloud|aws|azure|gcp", "slot": "topic", "value": "cloud"},
        {"pattern": "database|db|sql|nosql", "slot": "topic", "value": "database"},
        {"pattern": "api|rest|graphql", "slot": "topic", "value": "api"},
        {"pattern": "\\d+ phrases?|\\d+ sentences?|short|court|bref", "slot": "length", "value": "short"},
        {"pattern": "detailed|détaillé|long|complet|complete", "slot": "length", "value": "detailed"},
        {"pattern": "beginner|débutant|basic|base", "slot": "level", "value": "beginner"},
        {"pattern": "advanced|avancé|expert|pro", "slot": "level", "value": "advanced"}
    ],
    "configure": [
        {"pattern": "firewall|pare-feu", "slot": "component", "value": "firewall"},
        {"pattern": "dns", "slot": "component", "value": "dns"},
        {"pattern": "dhcp", "slot": "component", "value": "dhcp"},
        {"pattern": "vpn", "slot": "component", "value": "vpn"},
        {"pattern": "proxy", "slot": "component", "value": "proxy"},
        {"pattern": "nginx|apache|iis", "slot": "component", "value": "webserver"},
        {"pattern": "mysql|postgres|mariadb", "slot": "component", "value": "database"},
        {"pattern": "redis|memcached", "slot": "component", "value": "cache"},
        {"pattern": "ssl|tls|https", "slot": "component", "value": "ssl"},
        {"pattern": "ssh", "slot": "component", "value": "ssh"}
    ],
    "troubleshoot": [
        {"pattern": "error|erreur|bug|fail|échec", "slot": "issue", "value": "error"},
        {"pattern": "slow|lent|latency|latence", "slot": "issue", "value": "performance"},
        {"pattern": "crash|freeze|geler|hang|bloquer", "slot": "issue", "value": "crash"},
        {"pattern": "connection|connexion|network|reseau", "slot": "issue", "value": "network"},
        {"pattern": "permission|autorisation|access|accès", "slot": "issue", "value": "permission"},
        {"pattern": "memory|ram|mémoire", "slot": "issue", "value": "memory"},
        {"pattern": "disk|disque|storage|stockage", "slot": "issue", "value": "storage"},
        {"pattern": "cpu|processor|processeur", "slot": "issue", "value": "cpu"}
    ]
}

# === GÉNÉRATION DU DICTIONNAIRE ===
def generate_rules():
    rules = []
    
    # 1. Conjugaisons FR
    for infinitif, formes in VERBES_FR.items():
        for forme in formes:
            if len(forme) > 2:  # Évite les formes trop courtes
                rules.append({"pattern": forme, "replace": infinitif, "category": "conjugation_fr"})
    
    # 2. Conjugaisons EN
    for infinitif, formes in VERBES_EN.items():
        for forme in formes:
            if len(forme) > 2:
                rules.append({"pattern": forme, "replace": infinitif, "category": "conjugation_en"})
    
    # 3. Pluriels irréguliers FR
    for pluriel, singulier in PLURIELS_IRR_FR.items():
        rules.append({"pattern": pluriel, "replace": singulier, "category": "plurals_fr"})
    
    # 4. Pluriels irréguliers EN
    for pluriel, singulier in PLURIELS_IRR_EN.items():
        rules.append({"pattern": pluriel, "replace": singulier, "category": "plurals_en"})
    
    # 5. Fautes de frappe FR
    for faute, correction in TYPOS_FR.items():
        rules.append({"pattern": faute, "replace": correction, "category": "typos_fr"})
    
    # 6. Fautes de frappe EN
    for faute, correction in TYPOS_EN.items():
        rules.append({"pattern": faute, "replace": correction, "category": "typos_en"})
    
    # 7. Synonymes tech
    for abbr, full in TECH_SYNONYMES.items():
        if abbr != full:  # Évite les self-mappings
            rules.append({"pattern": abbr, "replace": full, "category": "tech_synonyms"})
    
    # 8. Politesse FR
    for phrase in POLITESSE_FR:
        rules.append({"pattern": phrase, "replace": "", "category": "politeness_fr"})
    
    # 9. Politesse EN
    for phrase in POLITESSE_EN:
        rules.append({"pattern": phrase, "replace": "", "category": "politeness_en"})
    
    # 10. Filler FR
    for phrase in FILLER_FR:
        rules.append({"pattern": phrase, "replace": "", "category": "filler_fr"})
    
    # 11. Filler EN
    for phrase in FILLER_EN:
        rules.append({"pattern": phrase, "replace": "", "category": "filler_en"})
    
    # Tri par longueur décroissante
    rules.sort(key=lambda r: len(r["pattern"]), reverse=True)
    
    return rules

# === ÉCRITURE DU JSON ===
rules = generate_rules()

output = {
    "version": 1,
    "generated": datetime.now().isoformat(),
    "total_rules": len(rules),
    "intents": INTENTS,
    "slots": SLOTS,
    "rules": rules
}

output_path = Path(r"D:\CacheForge\config\normalization_rules.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"✓ Generated {len(rules)} rules")
print(f"✓ Written to {output_path}")
print(f"✓ File size: {output_path.stat().st_size / 1024:.1f} KB")
