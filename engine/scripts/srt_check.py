#!/usr/bin/env python3
"""
Mechanical QA checks for English SRT subtitles produced by OCR (e.g. Blu-ray PGS -> SRT).

Finds structural problems, OCR character confusions, spacing/punctuation noise,
broken tags, encoding/house-style problems and inconsistencies. Timing is never checked.
It never modifies
the input file.

Usage:
  python srt_check.py input.srt --json issues.json      # run checks
  python srt_check.py input.srt --dump > cues.txt       # one cue per line, for reading

Optional dependency: pyspellchecker (pip install pyspellchecker). Without it the
dictionary-based checks fall back to the file's own vocabulary.
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

TS_RE = re.compile(
    r'^\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})(.*)$'
)
TAG_RE = re.compile(r'<[^>]*>|\{\\[^}]*\}')
TOKEN_RE = re.compile(r"(?:[^\W_]|\|)(?:[^\W_]|[|'’])*")

# Contractions OCR commonly drops the apostrophe from. Unambiguous -> error.
MISSING_APOS = {
    "dont": "don't", "didnt": "didn't", "doesnt": "doesn't", "isnt": "isn't",
    "wasnt": "wasn't", "werent": "weren't", "arent": "aren't", "havent": "haven't",
    "hasnt": "hasn't", "hadnt": "hadn't", "couldnt": "couldn't", "wouldnt": "wouldn't",
    "shouldnt": "shouldn't", "mustnt": "mustn't", "youre": "you're", "theyre": "they're",
    "youve": "you've", "theyve": "they've", "youll": "you'll", "theyll": "they'll",
    "youd": "you'd", "theyd": "they'd", "thats": "that's", "whats": "what's",
    "wheres": "where's", "theres": "there's", "heres": "here's", "whos": "who's",
    "shes": "she's", "itll": "it'll", "thatll": "that'll", "im": "I'm", "ive": "I've",
    "lm": "I'm", "lve": "I've", "lll": "I'll", "aint": "ain't", "yall": "y'all",
}
# Real words that are also apostrophe-less contractions -> only "check".
# (Very common words like were/well/its/hell are left to the read-through.)
AMBIG_APOS = {"cant": "can't", "wont": "won't", "hes": "he's"}
# l read for capital I as whole tokens.
L_FOR_I = {"l": "I", "lt": "It", "lf": "If", "ln": "In", "ls": "Is", "lsn't": "Isn't",
           "lt's": "It's", "lnto": "Into", "lts": "Its"}
# Character-group confusions to test against the dictionary.
SUBS = [("rn", "m"), ("cl", "d"), ("vv", "w"), ("m", "rn"), ("li", "h"), ("tb", "th"),
        ("wb", "wh"), ("ii", "ll"), ("0", "o"), ("1", "l"),
        ("5", "s"), ("1", "I")]
# Digit+letter tokens that are legitimate.
OK_ALNUM = re.compile(
    r"^(\d+(st|nd|rd|th|s|am|pm|a|b|k|m|mm|cm|km|kg|g|mg|ml|lb|lbs|ft|in|mph|kph|x|d|h|hr|hrs|min|sec|cc|hp|bhp|fps|mm|kw|v|w|gb|mb|p)"
    r"|['’]?\d+s|\d+x\d+|[A-Z]+\d+[A-Za-z]*|\d+[A-Z]+\d*|[A-Z]\d+[a-z]?)$"
)
ABBREV_BEFORE_DOT = re.compile(r"\b(Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|etc|Lt|Sgt|Capt|Col|Gen|Prof|No)\.$")

SEVERITY_ORDER = {"error": 0, "likely": 1, "check": 2, "info": 3}

# House style for Plex: UTF-8 without BOM, CRLF, only <i> tags, straight quotes, "..." ellipses,
# "- " dialogue dashes, ♪ padded with spaces.
TARGET_ENCODING = "utf-8"
TARGET_NEWLINE = "CRLF"

# Fansub / ripper credits. Strong markers -> likely; generic credit wording -> check
# (official discs carry "Subtitles by ..." lines too).
FANSUB_STRONG = re.compile(
    r"(?i)\b(re-?sync(?:ed|hed)?|sync(?:ed|hed)?(?: and \w+)? by|synchroni[sz](?:ed|ation)\b(?:.{0,20}\b(?:by|:))|ripped by|rip by|transcribed by|"
    r"fansub|corrected by|corrections? by|encoded by|timed by(?= [A-Z])|timing by(?= [A-Z])|subs by|opensubtitles|addic7ed|subscene|"
    r"podnapisi|yify|yts|rarbg|www\.|https?://|\S+\.(?:com|org|net|tv|io)\b)|(?<!\w)@\w")
FANSUB_WEAK = re.compile(r"(?i)\b(subtitles? by|subtitled by|translated by|translation\s*:|translation by|"
                         r"english subtitles|subtitles\s*:)")

# Majority-rule word variants. (label, [(form, regex)]). The most frequent form wins.
VARIANT_GROUPS = [
    ("OK spelling", [("OK", r"\bOK\b"), ("okay", r"\b[Oo]kay\b"), ("O.K.", r"\bO\.K\.(?!\w)"),
                     ("ok", r"\b[Oo]k\b")]),
    ("alright / all right", [("alright", r"\b[Aa]lright\b"), ("all right", r"\b[Aa]ll right\b")]),
    ("goodbye", [("goodbye", r"\b[Gg]oodbye\b"), ("good-bye", r"\b[Gg]ood-bye\b"),
                 ("good bye", r"\b[Gg]ood bye\b")]),
    ("grey / gray", [("grey", r"\bgrey\b"), ("gray", r"\bgray\b")]),
    ("dumbass", [("dumbass", r"\b[Dd]umbass(?:es)?\b"), ("dumb-ass", r"\b[Dd]umb-ass(?:es)?\b"),
                 ("dumb ass", r"\b[Dd]umb ass(?:es)?\b")]),
    ("toward / towards", [("toward", r"\b[Tt]oward\b"), ("towards", r"\b[Tt]owards\b")]),
    ("afterward / afterwards", [("afterward", r"\b[Aa]fterward\b"), ("afterwards", r"\b[Aa]fterwards\b")]),
]
ALWAYS_UPPER = {"OK", "O.K."}

# Proper nouns starting with I that a lowercase dictionary can't tell apart from common words.
I_PROPER = {"iran", "iranian", "iraq", "iraqi", "italy", "italian", "italians", "ireland", "irish", "india",
            "indian", "indians", "israel", "israeli", "istanbul", "iceland", "indonesia", "illinois", "indiana",
            "iowa", "idaho", "ivan", "igor", "irene", "isabel", "isabella", "ingrid", "isaac", "ian", "iris",
            "ida", "irving", "ike", "inez", "imelda", "isadora", "islam", "islamic", "inca", "indochina"}

# Words that start sentences far more often than they appear capitalised mid-sentence; a
# capitalised one after a comma usually means a full stop was read as a comma.
SENTENCE_STARTERS = {"he", "she", "it", "we", "they", "you", "the", "this", "that", "there", "what",
                     "but", "and", "so", "no", "yes", "my", "your", "his", "her", "our", "their",
                     "i'm", "it's", "don't", "let's", "come", "go", "look", "well", "now", "then",
                     "where", "why", "how", "who", "if", "when", "please", "thank", "thanks"}
# Interjections and casual spellings a dictionary doesn't know but subtitles use on purpose.
INTERJECTION = re.compile(r"^(?:a+h+|a+r+g+h+|a+w+|e+h+|e+r+|h+m+|h+a+(?:h+a+)*|h+e+h+e*|m+|m+h+m+|o+h+|o+w+|o+o+p+s+|"
                          r"p+s+t+|s+h+|u+g+h+|u+h+|u+m+|w+h+o+a+|w+o+w+|y+a+y+|y+e+a+h+|y+e+p+|y+u+p+|n+a+h+|"
                          r"n+o+p+e+|g+r+|b+r+|z+|t+s+k+|p+f+t*|h+u+h+|w+h+e+w+|y+o+|y+a+|a+h+a+|o+k+a+y+|o+o+h+)$", re.I)
# British spellings the American dictionary doesn't know and the regular -our/-ise mapping doesn't cover
BRITISH = {"jewellery", "jeweller", "jewellers", "manoeuvre", "manoeuvres", "manoeuvred", "manoeuvring", "dreamt",
           "learnt", "spelt", "spilt", "burnt", "leant", "leapt", "smelt", "clientele", "programme", "programmes",
           "cheque", "cheques", "kerb", "tyre", "tyres", "pyjamas", "aluminium", "storey", "storeys", "plough",
           "moustache", "sceptic", "sceptical", "draught", "gaol", "whilst", "amongst", "aeroplane", "mould",
           "grey", "licence", "practise", "practised", "enrol", "enrolment", "fulfil", "instalment", "skilful"}
CASUAL = {"bro", "arse", "arses", "bloke", "blokes", "quid", "mum", "mummy", "bloody", "blimey", "bollocks",
          "knackered", "gonna", "wanna", "gotta", "kinda", "sorta", "dunno", "lemme", "gimme", "outta", "lotta", "ya", "yeah",
          "nah", "okay", "alright", "whatcha", "gotcha", "betcha", "c'mon", "cause", "cos", "cuz", "em", "til",
          "y'all", "ain't", "innit", "mister", "missus", "ma'am", "nope", "yup"}
# Real words OCR produces from other real words (rn/m, ri/n, cl/d ...). Flagged as "check" only.
REAL_WORD_SLIPS = [
    (re.compile(r"\bthe darn(?=\s*(?:[.,!?]|$|every|is|was|at|and))"), "the dam", "'darn' for 'dam' (rn read for m)"),
    (re.compile(r"\barid\b"), "and", "'arid' for 'and' (n read as ri)"),
    (re.compile(r"\bmodem\b"), "modern", "'modem' for 'modern' (rn read as m)"),
    (re.compile(r"\btum\b"), "turn", "'tum' for 'turn' (rn read as m)"),
    (re.compile(r"(?:(?<=\bthe )|(?<=\ba ))comer\b"), "corner", "'comer' for 'corner' (rn read as m)"),
    (re.compile(r"(?<=, )hut\b"), "but", "'hut' for 'but' (b read as h)"),
]
# Translation grammar slips: (regex, replacement function, detail, severity)
IRREGULAR_PAST = {"ran": "run", "went": "go", "was": "be", "were": "be", "did": "do", "saw": "see", "came": "come",
                  "took": "take", "got": "get", "had": "have", "made": "make", "gave": "give", "knew": "know",
                  "thought": "think", "said": "say", "told": "tell", "felt": "feel", "left": "leave", "kept": "keep",
                  "brought": "bring", "bought": "buy", "ate": "eat", "drank": "drink", "sang": "sing", "swam": "swim",
                  "wrote": "write", "spoke": "speak", "found": "find", "heard": "hear", "met": "meet", "sat": "sit",
                  "stood": "stand", "understood": "understand", "forgot": "forget", "lost": "lose", "won": "win",
                  "slept": "sleep", "taught": "teach", "caught": "catch", "fought": "fight", "chose": "choose"}
GRAMMAR_PATTERNS = [
    (re.compile(r"\b((?:can|could|to|will|would|must|should|cannot|can't|couldn't|don't|didn't|won't|wouldn't|let|help|"
                r"just|barely|hardly)\s+)breath\b", re.I), lambda m: m.group(1) + "breathe",
     "'breath' (noun) used as the verb 'breathe'.", "likely"),
    (re.compile(r"\b((?:to|will|would|don't|didn't|gonna|can't|cannot|could|might|must|should|won't)\s+)loose\b", re.I),
     lambda m: m.group(1) + "lose", "'loose' used for the verb 'lose'.", "likely"),
    (re.compile(r"\b(dared? not) to\b", re.I), lambda m: m.group(1), "'dared not to' is ungrammatical.", "likely"),
    (re.compile(r"\b(used to )(" + "|".join(IRREGULAR_PAST) + r")\b"),
     lambda m: m.group(1) + IRREGULAR_PAST[m.group(2)], "Past tense after 'used to'.", "likely"),
    (re.compile(r"\b((?:did|didn't|does|doesn't|do|don't)\s+(?:(?:I|you|he|she|it|we|they)\s+)?"
                r"(?:(?:only|really|ever|just|even|never|also|still|actually|not)\s+)?)(" + "|".join(IRREGULAR_PAST) + r")\b", re.I),
     lambda m: m.group(1) + IRREGULAR_PAST[m.group(2)], "Past tense after 'did/does'.", "check"),
]
SUBJ_ING = re.compile(r"(?:(?<=^)|(?<=[.!?\-\"] )|(?<=, ))(He|She|They|We|I)\s+(\w+ing)\b")
ING_NOUNS = {"thing", "king", "ring", "wing", "sing", "bring", "spring", "string", "swing", "sting", "nothing",
             "something", "anything", "everything", "morning", "evening", "ceiling", "building", "wedding",
             "feeling", "meeting", "darling", "pudding", "during", "sibling", "duckling", "Beijing", "Nanjing"}
SINGLE_WORD_ITALIC = re.compile(r"<i>(I'll|I'm|I've|I'd|I|It's|It|so|as|a|an|the|and|to|of|in|is|or|but)</i>")

MONTHS_DAYS = {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

# Short words used to split merged tokens (ofthe -> of the)
SPLIT_WORDS = {"of", "the", "to", "in", "and", "a", "is", "it", "you", "i", "am", "on", "at", "for",
               "be", "me", "my", "we", "he", "she", "are", "was", "not", "that", "this", "with", "do",
               "go", "an", "as", "if", "or", "so", "no", "up"}

# Words a sentence almost never ends on. "...give me the." is usually a cut-off line
# whose interruption dash was read as a full stop.
NO_END_WORDS = {
    "the", "a", "an", "and", "but", "or", "nor", "my", "your", "our", "their", "if",
    "than", "very", "i'm", "i've", "i'll", "you're", "we're", "they're", "it's", "that's",
}
INTERRUPT_RE = re.compile(r"(?<=[^\s-])(\s*)(--|–|—|-)\s*(?:</i>)?\s*$")

# Loanwords whose unaccented spelling is a near-certain OCR loss.
ACCENTS_STRONG = {
    "senor": "señor", "senora": "señora", "senorita": "señorita", "senores": "señores",
    "fiance": "fiancé", "fiancee": "fiancée", "manana": "mañana", "jalapeno": "jalapeño", "jalapenos": "jalapeños", "pinata": "piñata",
    "deja": "déjà", "touche": "touché", "protege": "protégé", "attache": "attaché",
    "souffle": "soufflé", "creme": "crème", "brulee": "brûlée", "facade": "façade",
    "garcon": "garçon", "entree": "entrée", "fete": "fête", "naivete": "naïveté",
    "senoritas": "señoritas", "divorcee": "divorcée", "nee": "née", "monsieur": None,
}
# Commonly written either way in English; only flagged if the file also uses the accent.
ACCENTS_SOFT = {"cafe": "café", "cliche": "cliché", "naive": "naïve", "decor": "décor",
                "resume": "résumé", "expose": "exposé", "rose": "rosé", "role": "rôle",
                "elite": "élite", "melee": "mêlée", "regime": "régime", "pate": "pâté"}

# Abbreviations made of a word's first and last letters: Hart's rules drop the stop.
ABBR_CONTRACTION = ["Mr", "Mrs", "Ms", "Dr", "St", "Jr", "Sr", "Mt", "Sgt", "Lt", "Supt", "Revd", "Cmdr"]
# Truncations keep the stop in both American style and Hart's rules.
ABBR_TRUNCATION = ["Prof", "Capt", "Col", "Gen", "Gov", "Sen", "Maj", "Insp", "Brig", "Rev", "Det"]


# --------------------------------------------------------------------------- #
# Reading & parsing
# --------------------------------------------------------------------------- #
def read_file(path):
    raw = open(path, "rb").read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8-sig"), ("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def ts_to_ms(h, m, s, ms):
    ms = ms.ljust(3, "0")
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def ms_to_ts(ms):
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def parse(text):
    """Return (cues, structural_issues). Line numbers are 1-based file lines."""
    lines = text.splitlines()
    issues = []
    ts_positions = [i for i, l in enumerate(lines) if TS_RE.match(l)]
    cues = []

    if not ts_positions:
        issues.append(dict(cue=None, line_no=1, category="structure", severity="error",
                           text="", detail="No valid SRT timestamps found."))
        return cues, issues

    first_block = ts_positions[0] - 1 if ts_positions[0] > 0 and lines[ts_positions[0] - 1].strip().isdigit() else ts_positions[0]
    for i in range(first_block):
        if lines[i].strip():
            issues.append(dict(cue=None, line_no=i + 1, category="structure", severity="check",
                               text=lines[i], detail="Content before the first cue."))

    for n, p in enumerate(ts_positions):
        idx_line = p - 1 if p > 0 and lines[p - 1].strip().isdigit() else None
        nxt = ts_positions[n + 1] if n + 1 < len(ts_positions) else len(lines)
        nxt_idx = nxt - 1 if nxt < len(lines) and nxt > 0 and lines[nxt - 1].strip().isdigit() else nxt
        body = list(range(p + 1, nxt_idx))
        while body and not lines[body[-1]].strip():
            body.pop()
        m = TS_RE.match(lines[p])
        cue = dict(
            n=n + 1,
            index=int(lines[idx_line].strip()) if idx_line is not None else None,
            index_line_no=idx_line + 1 if idx_line is not None else None,
            ts_line_no=p + 1,
            start=ts_to_ms(*m.group(1, 2, 3, 4)),
            end=ts_to_ms(*m.group(5, 6, 7, 8)),
            text_lines=[(i + 1, lines[i]) for i in body],
        )
        cues.append(cue)

        if idx_line is None:
            issues.append(dict(cue=cue["n"], line_no=p + 1, category="structure", severity="error",
                               text=lines[p], detail="Cue has no index number above the timestamp.", fix="normalize"))
        if m.group(9).strip():
            issues.append(dict(cue=cue["n"], line_no=p + 1, category="structure", severity="info",
                               text=lines[p], detail="Extra data after timestamp (position/coordinates)."))
        if nxt < len(lines) and nxt_idx - 1 >= 0 and nxt_idx - 1 > p and lines[nxt_idx - 1].strip():
            issues.append(dict(cue=cue["n"], line_no=nxt_idx, category="structure", severity="error",
                               text=lines[nxt_idx - 1], detail="No blank line before the next cue.", fix="normalize"))
        for ln, t in cue["text_lines"]:
            if not t.strip():
                issues.append(dict(cue=cue["n"], line_no=ln, category="structure", severity="error",
                                   text="", detail="Blank line inside cue text (players may end the cue here).", fix="normalize"))
            if "-->" in t:
                issues.append(dict(cue=cue["n"], line_no=ln, category="structure", severity="error",
                                   text=t, detail="Malformed timestamp line absorbed as text."))
        if not any(t.strip() for _, t in cue["text_lines"]):
            issues.append(dict(cue=cue["n"], line_no=p + 1, category="structure", severity="error",
                               text="", detail="Cue has no text."))

    # Index sequence
    seen = Counter(c["index"] for c in cues if c["index"] is not None)
    for c in cues:
        if c["index"] is None:
            continue
        if seen[c["index"]] > 1:
            issues.append(dict(cue=c["n"], line_no=c["index_line_no"], category="structure", severity="error",
                               text=str(c["index"]), detail="Duplicate cue index.", fix="normalize"))
        elif c["index"] != c["n"]:
            issues.append(dict(cue=c["n"], line_no=c["index_line_no"], category="structure", severity="error",
                               text=str(c["index"]), detail=f"Index out of sequence (expected {c['n']}).", fix="normalize"))
    return cues, issues


# --------------------------------------------------------------------------- #
# Dictionary
# --------------------------------------------------------------------------- #
_SPELL = None
_PLACES = None
ACCEPTED = set()  # words dismissed in earlier read-throughs (srt_qa_lexicon.txt): names, slang, terms


BUNDLED_WORDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "accepted_words.txt")


def load_accepted(path=None):
    """Accepted words (one per line, # comments): the list bundled with the skill, plus any working list
    from this session. Unknown-word flags skip these."""
    ACCEPTED.clear()
    for p in (BUNDLED_WORDS, path):
        if p and os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                w = line.split("#", 1)[0].strip().lower()
                if w:
                    ACCEPTED.add(w)
    return ACCEPTED


class Lexicon:
    def __init__(self, cues):
        if not ACCEPTED:
            load_accepted()
        self.vocab = Counter()
        self.exact = Counter()  # case-sensitive token counts
        for c in cues:
            for _, t in c["text_lines"]:
                for tok in TOKEN_RE.findall(TAG_RE.sub("", t)):
                    self.vocab[norm(tok)] += 1
                    self.exact[tok.rstrip("'’")] += 1
        try:
            global _SPELL
            from spellchecker import SpellChecker
            if _SPELL is None:
                _SPELL = SpellChecker()
            self.spell = _SPELL
            self.mode = "pyspellchecker"
        except ImportError:
            self.spell = None
            self.mode = "file-vocabulary only"

    def known(self, word):
        w = norm(word)
        if not w or not re.search(r"[a-z]", w):
            return False
        if self.spell is not None:
            base = re.sub(r"['’]s$", "", w)
            if self.spell.known([w]) or self.spell.known([base]):
                return True
            us = us_key(base)  # British spelling of a known word: favour, metres, realise
            return us != base and bool(self.spell.known([us]))
        return self.vocab[w] >= 3  # fallback: frequent in this file

    def better(self, original, candidate):
        """Is candidate a plausible correction of original?"""
        if self.spell is not None:
            return self.known(candidate) and not self.known(original)
        return self.vocab[norm(candidate)] >= 2 and self.vocab[norm(original)] <= 1


def norm(tok):
    return tok.lower().replace("’", "'").strip("'")


def visible(line):
    return TAG_RE.sub("", line)


def vis_index(raw):
    """Visible text of a raw line and, for each visible char, its index in the raw line."""
    vis, idx, pos = [], [], 0
    for m in TAG_RE.finditer(raw):
        for k in range(pos, m.start()):
            vis.append(raw[k]); idx.append(k)
        pos = m.end()
    for k in range(pos, len(raw)):
        vis.append(raw[k]); idx.append(k)
    return "".join(vis), idx


def make_edit(raw, vs, ve, new, on_raw=False):
    """Exact edit for a span of the visible text (or of the raw line). None if a tag sits inside it."""
    if on_raw:
        rs, re_ = vs, ve
    else:
        vis, idx = vis_index(raw)
        if ve > vs:
            rs, re_ = idx[vs], idx[ve - 1] + 1
            if raw[rs:re_] != vis[vs:ve]:
                return None
        else:
            rs = re_ = idx[vs] if vs < len(idx) else (idx[-1] + 1 if idx else 0)
    if raw[rs:re_] == new:
        return None
    return {"start": rs, "end": re_, "old": raw[rs:re_], "new": new}


def fix_fields(raw, edit):
    """Fields stored on an issue: the exact edit plus the whole line as it would read after it."""
    if not edit:
        return {"edit": None, "proposed": None}
    return {"edit": edit, "proposed": raw[:edit["start"]] + edit["new"] + raw[edit["end"]:]}


def match_case(src, repl):
    if src[:1].isupper() and repl[:1].islower():
        return repl[:1].upper() + repl[1:]
    return repl


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def token_checks(tok, lex, sentence_start, after_hyphen=False, next_cap=False):
    """Yield (severity, detail, suggestion) for a single token."""
    low = norm(tok)
    bare = tok.replace("’", "'")

    if "|" in tok:
        cands = []
        if tok == "|":
            cands = ["I"]
        else:
            cands = [tok.replace("|", "I"), tok.replace("|", "l"),
                     "I" + tok[1:].replace("|", "l") if tok.startswith("|") else None]
            cands = [c for c in cands if c and (lex.known(c) or re.match(r"^I['’]", c))]
        yield ("likely", "Pipe character, usually OCR for I or l.",
               cands[0] if cands else tok.replace("|", "I"))
        return

    if bare in L_FOR_I:
        yield ("error", "Lowercase l read for capital I.", L_FOR_I[bare])
        return
    if re.fullmatch(r"l[bcdfghjkmnpqrstvwxz][a-z]+", tok) and tok not in ("lbs",):
        low_c = "i" + tok[1:]
        if lex.known(low_c) and not sentence_start and not next_cap and low_c not in I_PROPER \
                and lex.exact["I" + tok[1:]] == 0:
            yield ("error", "Lowercase l read for i (no English word starts with l + consonant).", low_c)
        else:  # a name (lvan), a sentence start, or a title before a name (lnspector Morse)
            yield ("error" if lex.known(low_c) else "likely",
                   "Lowercase l read for capital I (no English word starts with l + consonant).", "I" + tok[1:])
        return
    if re.fullmatch(r"l['](m|ll|d|ve)", bare):
        yield ("error", "Lowercase l read for capital I.", "I" + tok[1:])
        return
    if re.fullmatch(r"i(['’](m|ll|d|ve))?", tok):
        yield ("error", "Lowercase i for the pronoun I.", "I" + tok[1:])
        return
    if bare in ("Ill", "lll"):
        yield ("likely", "Probably I'll with the apostrophe lost.", "I'll")
        return

    if low in MISSING_APOS and "'" not in bare:
        yield ("error", "Contraction missing its apostrophe.", match_case(tok, MISSING_APOS[low]))
        return

    # Capital I inside a lowercase word: aII, wiII, feeI, IittIe
    if re.search(r"[a-z]", tok) and not tok.isupper():
        inner = [i for i, ch in enumerate(tok) if ch == "I" and i > 0 and
                 (tok[i - 1].islower() or (tok[i - 1].isupper() and tok[i + 1:i + 2].islower() and tok[i + 1:] != "s"))]
        starts_I = tok[0] == "I" and len(tok) > 1 and tok[1].islower() and \
            (lex.vocab["l" + low[1:]] > 0 or lex.vocab[low] <= 2)
        if inner or (starts_I and not lex.known(tok)):
            fixed = "".join("l" if (ch == "I" and (i in inner or (i == 0 and starts_I))) else ch for i, ch in enumerate(tok))
            if not inner and starts_I and sentence_start:
                fixed = "L" + fixed[1:]
            if lex.known(fixed):
                yield ("error", "Capital I read for lowercase l.", fixed)
                return
            if inner:
                yield ("likely", "Capital I inside a lowercase word.", fixed)
                return

    # Letter O read as zero inside a number: 1OO, 19O5, 1O:3O (token "1O"), 5O%, OO7
    if re.search(r"\d", tok) and re.fullmatch(r"[\dO]+", tok) and "O" in tok:
        sev = "likely" if re.fullmatch(r"O\d", tok) else "error"  # O2 can be oxygen
        yield (sev, "Letter O inside a number; should be zero.", tok.replace("O", "0"))
        return
    if re.fullmatch(r"\d+o\d+", tok):
        yield ("likely", "Letter o inside a number; probably zero.", tok.replace("o", "0"))
        return

    # Zero read as letter O inside a word: 0h, 0K, N0RTH, 0nly
    if "0" in tok and re.search(r"[A-Za-z]", tok) and not re.search(r"[1-9]", tok):
        chars = list(tok)
        for k, ch in enumerate(chars):
            if ch == "0":
                nb = (tok[k - 1] if k else "") + (tok[k + 1] if k + 1 < len(tok) else "")
                upper = any(c.isupper() for c in nb) or (k == 0 and sentence_start)
                chars[k] = "O" if upper else "o"
        cand = "".join(chars)
        if cand.isupper() or lex.known(cand) or lex.vocab[cand.lower()] > 0:
            yield ("error", "Zero read for the letter O.", cand)
            return

    # Digits mixed into words
    if re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok) and not OK_ALNUM.match(tok.strip("'’")):
        if re.search(r"[a-z]\d|\d[a-z]", tok):
            fixed = tok
            for d, l in (("0", "o"), ("5", "s"), ("1", "l")):
                fixed = fixed.replace(d, l)
            if fixed[0] == "l" and (sentence_start or lex.known("I" + fixed[1:])) and not lex.known(fixed):
                fixed = "I" + fixed[1:]
            yield ("likely", "Digit inside a word.", fixed if lex.known(fixed) else None)
            return

    # Character-group confusions (rn/m, cl/d, tb/th ...)
    if re.search(r"[a-z]", tok) and len(low) > 2 and not lex.known(tok) and not after_hyphen:
        for a, b in SUBS:
            if a in tok:
                cand = tok.replace(a, b)
                if lex.vocab[low] >= 3 and lex.vocab[norm(cand)] == 0:
                    continue  # used consistently (Iemon, Horner): a name, not a misread
                if lex.better(tok, cand):
                    yield ("likely", f"Possible OCR confusion '{a}' -> '{b}'.", cand)
                    return

    # Odd mixed case: tHe, wHat
    if re.search(r"[a-z][A-HJ-Z]", tok) and not re.match(r"^(Mc|Mac|O'|D'|De|La|Le|Van|Von)", tok) and not re.match(r"^[a-z]*[A-Z]?[a-z]+[A-Z][a-z]+$", tok):
        yield ("check", "Unusual mixed case.", None)
        return

    if low in AMBIG_APOS and "'" not in bare:
        yield ("info", f"Could be '{AMBIG_APOS[low]}' with a lost apostrophe; check context.", None)


SP = "[ \u00a0\u202f\u2009]"  # space, no-break space, narrow no-break, thin
LINE_PATTERNS = [
    # (regex, severity, detail, replacement or None). Run on visible text unless the
    # detail mentions "tag", in which case on the raw line.
    (re.compile(r"(?<=\S) {2,}(?=\S)"), "error", "Double space.", " "),
    (re.compile(rf"(?<=[A-Za-z0-9\"'’)\]]){SP}+(?=[,!?;:]+(?![A-Za-z]))(?!: ?\d\d\b)"), "error", "Space before punctuation.", ""),
    (re.compile(rf"(?<=[A-Za-z]){SP}+(?=\.(?!\.)(?:\s|$))"), "error", "Space before full stop.", ""),
    (re.compile(rf"(?<=[A-Za-z]){SP}+(?=(?:\.\.\.|…)\s*$)"), "likely", "Space before closing ellipsis.", ""),
    (re.compile(r"[\u00a0\u202f\u2009]"), "likely", "Non-breaking or thin space.", " "),
    (re.compile(r"(?<=[A-Za-z0-9\"')\]][,!?;])(?=[A-Za-z])"), "error", "Missing space after punctuation.", " "),
    (re.compile(r"(?<=[a-z]{2}\.)(?=[A-Z][a-z])"), "likely", "Missing space after full stop.", " "),
    (re.compile(rf"([\[(]){SP}*([^\[\]()]*?){SP}*([\])])"), "error", "Padding inside SDH bracket.",
     lambda m: None if m.group(0) == m.group(1) + m.group(2) + m.group(3) else m.group(1) + m.group(2) + m.group(3)),
    (re.compile(r"(?<![.\d])\.\.(?!\.)"), "likely", "Two dots; house style is '...'.", "..."),
    (re.compile(r"…"), "error", "Unicode ellipsis; house style is '...'.", "..."),
    (re.compile(r"[‘’]"), "error", "Curly apostrophe; house style is straight quotes.", "'"),
    (re.compile(r"[“”„]"), "error", "Curly double quote; house style is straight quotes.", '"'),
    (re.compile(r"(?<=[A-Za-z])[´`](?=[A-Za-z])"), "error", "Accent mark used as apostrophe.", "'"),
    (re.compile(r"\.{4,}"), "likely", "Four or more dots.", "..."),
    (re.compile(r",,|\.,|,\.(?!\.)|;;|::"), "error", "Doubled or mixed punctuation.", None),
    (re.compile(r"''|’’|``"), "error", "Doubled apostrophe; should be a double quote.", '"'),
    # spaces inside contractions: don 't / don' t
    (re.compile(r"(?<=[A-Za-z]) +(?=['’](?:s|t|m|ll|re|ve|d)\b)"), "error", "Space inside a contraction.", ""),
    (re.compile(r"(?<=[A-Za-z]['’]) +(?=(?:s|t|m|ll|re|ve|d)\b)"), "error", "Space inside a contraction.", ""),
    # quote padding at line edges
    (re.compile(r'(?:(?<=^)|(?<=[\s(\[]))" +(?=\w)'), "error", "Space just inside a quotation mark.",
     lambda m: '"' if m.string[:m.start()].count('"') % 2 == 0 else None),
    (re.compile(r'(?<=\w) +"(?=\s*$|[\s.,!?;:)\]])'), "error", "Space just inside a quotation mark.",
     lambda m: '"' if m.string[:m.start()].count('"') % 2 == 1 else None),
    # missing spaces
    (re.compile(r"(?<=[A-Za-z]:)(?=[A-Za-z])"), "error", "Missing space after colon.", " "),
    # numbers
    (re.compile(r"(?<![\d,.] )(?<![\d,.])\b(\d{1,3}) (\d{3})\b(?! ?\d)"), "likely", "Space inside a number.",
     lambda m: m.group(1) + m.group(2)),
    (re.compile(r"(?<=\d) ?: (?=\d\d\b)|(?<=\d) :(?=\d\d\b)"), "error", "Space inside a time.", ":"),
    (re.compile(r"(?<=[$£€¥]) +(?=\d)"), "error", "Space after currency symbol.", ""),
    (re.compile(r"(?<=\d) +(?=%)"), "error", "Space before percent sign.", ""),
    # stray apostrophe (often with a dot) before ? or !: Who'? / money'.! (not g-dropping: nothin'?)
    (re.compile(r"(?<=[A-Za-rt-z])(?<!in)'\.?(?=[?!])"), "error", "Stray apostrophe before ? or ! (OCR noise).",
     lambda m: None if re.search(r"(?:^|[^A-Za-z])'[A-Za-z]", m.string[:m.start()]) else ""),
    (re.compile(r"(?<=s)'\.?(?=[?!])(?<!\s')"), "likely",
     "Stray apostrophe before ? or ! (OCR noise), unless it's a plural possessive (the Joneses'?).",
     lambda m: None if re.search(r"(?:^|[^A-Za-z])'[A-Za-z]", m.string[:m.start()]) else ""),
    # a lone lowercase u/o between words is almost always an OCR'd "a" ("being u jerk", "doing me o favor")
    (re.compile(r"(?<=[A-Za-z,] )[uo](?= [A-Za-z])"), "likely", "Lone 'u' or 'o'; usually OCR for 'a'.", "a"),
    # Spanish opening marks misread by OCR: "!Vamos!" -> "¡Vamos!", "?Qué?" -> "¿Qué?"
    (re.compile(r"(?:^|(?<=[\s\"'(\[-]))!(?=[A-Za-zÁÉÍÓÚÑáéíóúñ])"), "likely",
     "Inverted exclamation mark misread as '!'.", "¡"),
    (re.compile(r"(?:^|(?<=[\s\"'(\[-]))\?(?=[A-Za-zÁÉÍÓÚÑáéíóúñ])"), "likely",
     "Inverted question mark misread as '?'.", "¿"),
    # a lone "[" (no "]" on the line) is a capital I misread by OCR: "Have [ got to", "[t's", "[f you"
    (re.compile(r"(?:^|(?<=[\s\"'(-]))\[(?=\s+[a-z]|['’](?:m|ll|d|ve)\b|(?:t|f|n|s)\b|t['’]s\b)"), "likely",
     "'[' read for a capital I.", lambda m: None if "]" in m.string else "I"),
    # "no one" is never hyphenated (house style)
    (re.compile(r"\b([Nn])o-one\b"), "error", "\"no one\" is two words (house style).", lambda m: m.group(1) + "o one"),
    # unneeded periods: What?. / Stop!. / Wait.?
    (re.compile(r"(?<=[!?])\.(?!\.)"), "error", "Unneeded full stop after ? or !.", ""),
    (re.compile(r"(?<![.])(?<!\b[A-Za-z])\.(?=[?!])"), "error", "Unneeded full stop before ? or !.",
     lambda m: None if re.search(r"\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|Lt|Col|Gen|Maj|Capt|Sgt|Cpl|Prof|Rev|Det|Insp|Supt|Gov|Sen|"
                                 r"No|vs|etc|Co|Inc|Ltd|Ave|Rd|Mt|Ft)$", m.string[:m.start()]) else ""),
    # invisible and look-alike characters (normalise)
    (re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]"), "error", "Invisible character (zero-width or soft hyphen).", ""),
    (re.compile(r"[\u2010\u2011\u2012\u2043]"), "error", "Look-alike hyphen character; use a plain hyphen.", "-"),
    (re.compile(r"[\uff1a\u02f8\ufe13]"), "error", "Look-alike colon character; use a plain colon.", ":"),
    (re.compile(r"İ"), "error", "Look-alike letter (dotted İ); use a plain I.", "I"),
    # apostrophes misread as double quotes: don"t, "cause
    (re.compile(r'(?<=[A-Za-z])"(?=(?:s|t|m|ll|re|ve|d)\b)'), "error", "Double quote misread for an apostrophe.", "'"),
    (re.compile(r'(?<![\w"])"(?=(?:[Cc]ause|em|til|bout|round|[Tt]was|[Tt]is|kay|scuse|nuff|cos|cuz)\b)'), "likely",
     "Double quote misread for an apostrophe.", lambda m: "'" if m.string.count('"') % 2 == 1 else None),
    # comma/period confusions
    (re.compile(r",(?=[?!])"), "error", "Comma before ? or !.", ""),
    (re.compile(r"(?<=[?!]),"), "error", "Comma after ? or !.", ""),
    (re.compile(r"[~^_@¬¦§¤«»{}\\]|(?<![A-Za-z])`|`(?![A-Za-z])"), "likely", "Unexpected character, probably OCR noise.", None),
    (re.compile(r"[\ufb00-\ufb06]"), "error", "Typographic ligature from OCR (ﬁ, ﬂ...); use plain letters.",
     lambda m: unicodedata.normalize("NFKC", m.group(0))),
    (re.compile(r"(?<![A-Za-z] )#(?![\d?])"), "likely", "Hash sign, often a misread music note.",
     lambda m: ((" " if m.start() and not m.string[m.start() - 1].isspace() and m.string[m.start() - 1] != ">" else "")
                + "♪" + (" " if m.end() < len(m.string) and not m.string[m.end()].isspace() and m.string[m.end()] != "<" else ""))),
    (re.compile(r"¶"), "likely", "Pilcrow, usually a misread music note.", "♪"),
    (re.compile(r"^\s*J(?=\s)|(?<=\s)J(?=\s*$)"), "likely", "Lone J, usually a misread music note.",
     lambda m: m.group(0).replace("J", "♪")),
    # ♪ padding
    (re.compile(r"(?<=[\w.])♪(?=[\w.])"), "likely", "Music note inside a word; probably a replaced letter.", None),
    (re.compile(r"♪(?=[^\s♪<.])"), "error", "No space after music note; house style is '♪ lyric ♪'.", "♪ "),
    (re.compile(r"(?<=[^\s♪>.])♪"), "error", "No space before music note; house style is '♪ lyric ♪'.", " ♪"),
    # an OCR'd dash wrapped in its own italic tags: <i>—</i> Beat it! (raw line; detail mentions tag)
    (re.compile(r"^(\s*)<i>\s*[-–—]\s*</i>\s*"), "error", "Dialogue dash wrapped in its own italic tags; house style is '- Text'.",
     lambda m: m.group(1) + "- "),
    # dialogue dash spacing
    (re.compile(r"^(\s*(?:<i>)?\s*)-(?=[^\s\-\d.])"), "error", "Dialogue dash without a space; house style is '- Text'.",
     lambda m: m.group(1) + "- "),
    (re.compile(r"^(\s*(?:<i>)?\s*)[–—]\s*(?=[^\s\-a-z])"), "likely", "En/em dash as dialogue dash; house style is '- Text'.",
     lambda m: m.group(1) + "- "),
    (re.compile(r"(?<=[.!?…] )[–—]\s*(?=[A-Z0-9\[(¡¿]|\.\.\.|…)"), "likely", "En/em dash as dialogue dash; house style is '- Text'.", "- "),
    (re.compile(r"\b(the|a|an|to|of|and|in|on|for|with|at|by|from|as|is|was|are|be|have|has|or|but) \1\b", re.I),
     "likely", "Repeated word.", None),
    (re.compile(r"(?<![.])\.( +)(?=[a-z])"), "likely",
     "Full stop before a lowercase word; probably a misread comma.", lambda m: "," + m.group(1)),
    (re.compile(r"(?<=[?!])( +)([a-z])"), "likely", "Lowercase after ? or !.",
     lambda m: m.group(1) + m.group(2).upper()),
    # tag patterns (raw line)
    (re.compile(r"</?\s+[iI]\s*>|<\s*[iI]\s+>|</?l>|</?[\[(]i[\])]>"), "error", "Malformed formatting tag.", None),
    (re.compile(r"</?(?:[bBuUsScCvV]|[Ff][Oo][Nn][Tt]|span|ruby|rt)(?:\s[^>]*)?>|</?I>"), "error",
     "Disallowed tag; only <i> is allowed for Plex compatibility.",
     lambda m: m.group(0).lower() if m.group(0) in ("<I>", "</I>") else ""),
    (re.compile(r"\{\\an\d\}"), "error",
     "Positioning tag (not supported on all clients); removing it puts the cue at the bottom.", ""),
    (re.compile(r"\{\\(?!an\d\})[^}]*\}"), "error", "ASS override tag; not supported on all clients.", ""),
    (re.compile(r"(?<=[A-Za-z])</?i>(?=[A-Za-z])", re.I), "error", "Italic tag inside a word.", None),
    # SDH bracket padding with an italic tag in between: "(gau go</i> )" / "( <i>sighs"
    (re.compile(r"(?<=</i>) +(?=[\])])|(?<=[\[(]) +(?=<i>)", re.I), "error", "Padding inside SDH bracket (next to a tag).", ""),
    (re.compile(r"</i><i>", re.I), "error", "Italic tag closed and reopened; merge.", ""),
    (re.compile(r"(?<=</i>) (?=<i>)|(?<=</I>) (?=<I>)"), "error", "Italic tag closed and reopened around a space; merge.", None),
    (re.compile(r"<i>\s*</i>", re.I), "error", "Empty italic tag pair.", ""),
    (re.compile(r"^(\s*<i>)\s+", re.I), "error", "Space just inside opening tag.", None),
    (re.compile(r"\s+(?=</i>\s*$)", re.I), "error", "Space just inside closing tag.", ""),
]


DASH_START = re.compile(r"^(\s*(?:<i>)?\s*)([-–—](?!-)\s*)")


CC_MARK = re.compile(r"^(\s*(?:<i>)?\s*)(>{2,3})\s*")


def cc_markers(cue, issues):
    """Closed-caption markers: '>>' = new speaker, '>>>' = new topic. Two speakers in the cue ->
    '>>' becomes '- '; one speaker -> deleted. '>>>' is always deleted."""
    tl = [(ln, t) for ln, t in cue["text_lines"] if t.strip()]
    marks = [(ln, t, CC_MARK.match(t)) for ln, t in tl]
    if not any(m for _, _, m in marks):
        return
    speakers = sum(1 for _, t, m in marks
                   if (m and m.group(2) == ">>") or re.match(r"^\s*(?:<i>)?\s*[-–—]\s*\S", t))
    for ln, t, m in marks:
        if not m:
            continue
        if m.group(2) == ">>>":
            new, why = "", "Closed-caption '>>>' topic marker."
        elif speakers >= 2:
            new, why = "- ", "Closed-caption '>>' speaker marker; two speakers, so it becomes a dialogue dash."
        else:
            new, why = "", "Closed-caption '>>' speaker marker; one speaker, so it's removed."
        issues.append(dict(cue=cue["n"], line_no=ln, category="dialogue", severity="likely", text=t, detail=why,
                           **fix_fields(t, make_edit(t, m.start(2), m.end(), new, on_raw=True))))


def two_speakers_one_line(cue, issues):
    """'- Hi. - Hello.' on a single line -> each speaker on their own line, both dashed.
    This is the one re-wrap the house style allows."""
    lines = [(ln, t) for ln, t in cue["text_lines"] if t.strip()]

    def is_caption(t):
        # a scene caption or sign translation: ALL CAPS, or wholly in brackets
        v = visible(t).strip()
        return (re.search(r"[A-Z]", v) and v == v.upper()) or bool(re.fullmatch(r"[\[(].*[\])]", v))

    for ln, t in cue["text_lines"]:
        v = visible(t)
        if re.search(r"\s[-–—]\s*$", v):
            continue  # paired dashes around an aside
        others = [o for o_ln, o in lines if o_ln != ln]
        if others and not all(is_caption(o) for o in others):
            continue  # splitting would make three dialogue lines; leave the cue as it is
        m = re.search(r"([.!?…\"])(\s+)([-–—])\s*(?=[A-Z0-9\[(¡¿]|\.\.\.|…)", t)
        if not m:
            continue
        head = t[:m.start(2)]
        tail = t[m.end():]
        lead = re.match(r"^\s*(?:<i>)?\s*", head).end()
        if not re.match(r"[-–—]", head[lead:]):
            head = head[:lead] + "- " + head[lead:]
        else:
            head = head[:lead] + re.sub(r"^[-–—]\s*", "- ", head[lead:])
        new = head + "\n- " + tail
        issues.append(dict(cue=cue["n"], line_no=ln, category="dialogue", severity="likely", text=t,
                           detail="Two speakers on one line; put each speaker on their own line.",
                           **fix_fields(t, make_edit(t, 0, len(t), new, on_raw=True))))


def single_speaker_dashes(cue, issues):
    """Remove a dialogue dash when the cue has only one speaker and no SDH line."""
    tl = [(ln, t) for ln, t in cue["text_lines"] if t.strip()]
    if not tl:
        return set()
    vis_all = " ".join(visible(t) for _, t in tl)
    if re.search(r"[\[(]", vis_all):
        return set()  # SDH present: the dash separates a sound source from a speaker
    if re.search(r"\s[-–—]\s*$", visible(tl[0][1])):
        return set()  # paired dashes around an aside: "- but is it only that? -"
    dashed = [bool(DASH_START.match(t)) for _, t in tl]
    removed = set()
    if dashed[0] and not any(dashed[1:]):
        ln, t = tl[0]
        if any(re.search(r"[.!?…\"]\s+[-–—]\s*(?:[A-Z0-9\[(¡¿]|\.\.\.|…)", visible(x)) for _, x in tl):
            return set()  # a second speaker starts mid-line ("... Formation A. — Roger!")
        if len(tl) == 1:
            sev, why = "likely", "Dialogue dash on a one-line, one-speaker cue."
        else:
            nxt = visible(tl[1][1]).lstrip(" <>i♪\"'")
            first_end = visible(t).rstrip(" </>i")
            if nxt[:1].islower() or not re.search(r"[.!?…\"'♪]$", first_end):
                sev, why = "likely", "Dialogue dash on a one-speaker cue (line 2 continues line 1's sentence)."
            else:
                return set()  # could be a lost dash on line 2; the read-through decides
        m = DASH_START.match(t)
        issues.append(dict(cue=cue["n"], line_no=ln, category="dialogue", severity=sev, text=t, detail=why,
                           **fix_fields(t, make_edit(t, m.start(2), m.end(2), "", on_raw=True))))
        removed.add(ln)
    return removed


def line_checks(cue, issues, lex):
    tag_open = Counter()
    brackets = Counter()
    dash_removed = single_speaker_dashes(cue, issues)
    prev_ended = True  # start of cue counts as sentence start for capitalization guess
    for k, (ln, raw) in enumerate(cue["text_lines"], 1):
        if not raw.strip():
            continue
        vis = visible(raw)
        if raw != raw.rstrip() or (raw != raw.lstrip()):
            issues.append(dict(cue=cue["n"], line_no=ln, category="spacing", severity="error",
                               text=raw, detail="Leading or trailing whitespace.",
                               **fix_fields(raw, make_edit(raw, 0, len(raw), raw.strip(), on_raw=True))))

        if unicodedata.normalize("NFC", raw) != raw:
            issues.append(dict(cue=cue["n"], line_no=ln, category="punctuation", severity="error", text=raw,
                               detail="Accented letters stored as two characters; normalise (NFC).",
                               **fix_fields(raw, make_edit(raw, 0, len(raw), unicodedata.normalize("NFC", raw),
                                                           on_raw=True))))
        for rx, sev, detail, repl in LINE_PATTERNS:
            target = raw if "tag" in detail else vis
            for m in rx.finditer(target):
                r = repl(m) if callable(repl) else repl
                if callable(repl) and r is None:
                    continue
                if detail.startswith("Doubled or mixed") and m.group(0) == ".," and \
                        re.search(r"\b(?:[A-Z][A-Za-z]{0,3}|etc|[a-z])$", target[:m.start()]):
                    continue  # "Co., Ltd." / "etc.," / "a.m.,"
                if detail.startswith("Full stop before a lowercase") and re.match(
                        r"\s*(?:l|i)(?:\b|['’])|\s*l[bcdfghjkmnpqrstvwxz]", target[m.end() - 1:] if m.group(0).endswith(" ") else target[m.end():]):
                    continue  # "better. l promise": the l is an OCR'd I, not a lowercase word
                if detail.startswith("Full stop before a lowercase") and (
                        ABBREV_BEFORE_DOT.search(target[:m.start() + 1])
                        or re.search(r"\b(?:[A-Za-z]\.){2,}$", target[:m.start() + 1])):
                    continue
                if ln in dash_removed and detail.startswith(("Dialogue dash without", "En/em dash as dialogue")):
                    continue
                if detail.startswith(("En/em dash as dialogue", "Dialogue dash without")) and \
                        re.match(r"^\s*<i>\s*[-–—]\s*</i>", raw):
                    continue  # handled by the italic-wrapped dash rule
                edit = make_edit(raw, m.start(), m.end(), r, on_raw=(target is raw)) if r is not None else None
                if edit is None and detail == "Padding inside SDH bracket.":
                    continue  # a tag sits inside the padding: the tag-aware padding rule fixes it
                cat = ("tags" if "tag" in detail else "music" if "music" in detail else
                       "sdh" if "SDH" in detail else "interrupt" if "interruption" in detail else
                       "spacing" if "space" in detail.lower() else "punctuation")
                issues.append(dict(cue=cue["n"], line_no=ln, category=cat, severity=sev, text=raw,
                                   detail=detail, **fix_fields(raw, edit)))

        for tm in TOKEN_RE.finditer(vis):
            tok = tm.group(0).rstrip("'’")
            if tm.start() > 0 and vis[tm.start() - 1] in "'’" and (tm.start() < 2 or not vis[tm.start() - 2].isalpha()) \
                    and not re.fullmatch(r"i(['’](m|ll|d|ve))?", tm.group(0)):
                continue  # 'im, 'em, 'cause: dialect elision, not OCR
            before = vis[:tm.start()].rstrip()
            sentence_start = (k == 1 and not before) or before.endswith((".", "!", "?", "-", "—"))
            after_hyphen = tm.start() > 0 and vis[tm.start() - 1] == "-" and tm.start() > 1 and vis[tm.start() - 2].isalpha()
            found = False
            if tok.endswith("|") and re.search(r"\[[^\]]*$", vis[:tm.start() + len(tok) - 1]):
                continue  # '[coughs|': handled as a closing bracket by the SDH rule
            next_cap = bool(re.match(r"\s+[A-Z]", vis[tm.end():]))
            for sev, detail, sugg in token_checks(tok, lex, sentence_start, after_hyphen, next_cap):
                found = True
                edit = make_edit(raw, tm.start(), tm.start() + len(tok), sugg) if sugg else None
                issues.append(dict(cue=cue["n"], line_no=ln, category="ocr", severity=sev, text=raw,
                                   detail=detail, token=tok, **fix_fields(raw, edit)))
            if not found and lex.spell is not None and tok.isalpha() and len(tok) >= 4 and not lex.known(tok) \
                    and lex.vocab[tok.lower()] <= 2 and (tok[0].islower() or re.fullmatch(r"I[a-z]+", tok)) \
                    and not vis[tm.start() + len(tok):tm.start() + len(tok) + 1] in ("'", "’") \
                    and not (tok.endswith("in") and lex.known(tok + "g")):
                splits = []
                for cut in range(1, len(tok)):
                    a_, b_ = tok[:cut], tok[cut:]
                    if (a_.lower() in SPLIT_WORDS or b_.lower() in SPLIT_WORDS) and lex.known(a_) and lex.known(b_) \
                            and (len(a_) > 1 or a_ in "aAI") and (len(b_) > 1 or b_ in "aI"):
                        wf = lex.spell.word_frequency
                        splits.append((min(wf[a_.lower()], wf[b_.lower()]), a_, b_))
                if splits:
                    _, a_, b_ = max(splits)  # "thereis" -> "there is", not "the reis"
                    issues.append(dict(cue=cue["n"], line_no=ln, category="spacing", severity="likely", text=raw,
                                       detail="Missing space between words.", token=tok,
                                       **fix_fields(raw, make_edit(raw, tm.start(), tm.start() + len(tok),
                                                                   a_ + " " + b_))))
                else:
                    pass
            if not found and lex.spell is not None and tok.isascii() and tok.isalpha() and len(tok) >= 3 \
                    and (tok.islower() or (sentence_start and tok[0].isupper() and tok[1:].islower())) \
                    and not lex.known(tok) and lex.vocab[tok.lower()] <= (2 if tok.islower() else 1) \
                    and not re.match(r"\.\.\.|…|-(?:\s|$|-)", vis[tm.start() + len(tok):tm.start() + len(tok) + 3]) \
                    and tok.lower() not in CASUAL and tok.lower() not in ACCEPTED and tok.lower() not in BRITISH \
                    and not INTERJECTION.match(tok) \
                    and vis[tm.start() + len(tok):tm.start() + len(tok) + 1] not in ("'", "’") \
                    and not after_hyphen and not any(i.get("line_no") == ln and i.get("token") == tok for i in issues):
                # one-edit suggestions only (two-edit search is slow and rarely right for OCR)
                cands = [w for w in lex.spell.edit_distance_1(tok.lower()) if w in lex.spell.word_frequency]
                sugg = max(cands, key=lambda w: lex.spell.word_frequency[w]) if cands else None
                sugg = match_case(tok, sugg) if sugg else None
                issues.append(dict(cue=cue["n"], line_no=ln, category="ocr", severity="check", text=raw,
                                   detail="Unknown word (not in the dictionary)." + (f" Dictionary's nearest: '{sugg}'."
                                                                                      if sugg else ""),
                                   token=tok, edit=None, proposed=None))  # a hint for the read-through, never a fix

        for zm in re.finditer(r"(?<![\w$£€.,:])0(?= [A-Z][a-z])", vis):
            issues.append(dict(cue=cue["n"], line_no=ln, category="ocr", severity="likely", text=raw,
                               detail="Standalone zero before a capitalised word; probably the vocative O.",
                               token="0", **fix_fields(raw, make_edit(raw, zm.start(), zm.end(), "O"))))

        for cm in re.finditer(r",( +)([A-Z][a-z']+)\b", vis):
            w = cm.group(2)
            prev_w = (re.findall(r"[A-Za-z']+", vis[:cm.start()]) or [""])[-1]
            next_cap = re.match(r"\s+[A-Z]", vis[cm.end():])
            if w.lower() in SENTENCE_STARTERS and lex.exact[w.lower()] >= 2 and not next_cap \
                    and prev_w.lower() != w.lower():
                issues.append(dict(cue=cue["n"], line_no=ln, category="punctuation", severity="check", text=raw,
                                   detail=f"Comma before capitalised '{w}'; probably a full stop read as a comma "
                                          "(or the capital is wrong).",
                                   **fix_fields(raw, make_edit(raw, cm.start(), cm.start() + 1, "."))))
        for sm in re.finditer(r"(?<=[a-z,] )([A-Z][a-z]+)\b", vis):
            w = sm.group(1)
            near = [x.strip("'") for x in re.findall(r"[A-Za-z']+", vis[:sm.start()])[-2:]
                    + re.findall(r"[A-Za-z']+", vis[sm.end():])[:2]]
            if any(x[:1].isupper() and x not in ("I", "I'm", "I'll", "I've", "I'd") for x in near):
                continue  # part of a capitalised title or name: Goddess of Mercy, Angel of Death
            if w in MONTHS_DAYS or re.match(r"\s+\d", vis[sm.end():]):
                continue  # May 1st, Monday
            if vis[:sm.start()].count('"') % 2 == 1 or re.search(r"(?:^|\s)'[^']*$", vis[:sm.start()]):
                continue  # inside a quoted title: "Ta-ra-ra Boom-de-ay", 'Dancing Girl'

            if w.lower() in SENTENCE_STARTERS | SPLIT_WORDS or (lex.known(w.lower()) and lex.exact[w.lower()] >= 2):
                if lex.exact[w] <= 1 and lex.exact[w.lower()] >= 2 and not vis[:sm.start()].rstrip().endswith(","):
                    issues.append(dict(cue=cue["n"], line_no=ln, category="case", severity="check", text=raw,
                                       detail=f"Capital '{w}' mid-sentence; lowercase everywhere else in the file.",
                                       **fix_fields(raw, make_edit(raw, sm.start(), sm.end(), w.lower()))))
        if vis.strip() in ("-", "–", "—"):
            issues.append(dict(cue=cue["n"], line_no=ln, category="structure", severity="error", text=raw,
                               detail="Line contains only a dash.", edit=None, proposed=None, fix="delete_line"))

        for rx, new, why in REAL_WORD_SLIPS:
            for sm in rx.finditer(vis):
                issues.append(dict(cue=cue["n"], line_no=ln, category="ocr", severity="check", text=raw,
                                   detail=f"Possible real-word OCR error: {why}.", token=sm.group(0),
                                   **fix_fields(raw, make_edit(raw, sm.start(), sm.end(), new))))
        for rx, fn, why, sev in GRAMMAR_PATTERNS:
            for gm in rx.finditer(vis):
                issues.append(dict(cue=cue["n"], line_no=ln, category="grammar", severity=sev, text=raw,
                                   detail=why, **fix_fields(raw, make_edit(raw, gm.start(), gm.end(), fn(gm)))))
        for gm in SUBJ_ING.finditer(vis):
            verb = gm.group(2)
            if verb in ING_NOUNS or not (lex.known(verb[:-3]) or lex.known(verb[:-3] + "e")
                                         or lex.known(verb[:-4])):
                continue
            aux = {"He": "He's", "She": "She's", "They": "They're", "We": "We're", "I": "I'm"}[gm.group(1)]
            issues.append(dict(cue=cue["n"], line_no=ln, category="grammar", severity="check", text=raw,
                               detail=f"'{gm.group(1)} {verb}' is missing a verb (OCR may have dropped an apostrophe).",
                               **fix_fields(raw, make_edit(raw, gm.start(1), gm.end(1), aux))))
        for im in SINGLE_WORD_ITALIC.finditer(raw):
            if TAG_RE.sub("", raw.replace(im.group(0), "")).strip(" .,!?-"):
                sev = "likely" if im.group(1).startswith("I") else "check"
                issues.append(dict(cue=cue["n"], line_no=ln, category="italics", severity=sev, text=raw,
                                   detail=f"One short word in italics ('{im.group(1)}'); usually an OCR artifact, not emphasis.",
                                   **fix_fields(raw, make_edit(raw, im.start(), im.end(), im.group(1), on_raw=True))))

        # Line ends on a word that can't end a sentence, followed by a full stop.
        mend = re.search(r"(?<![^\s>\"“])([^\W\d_]+(?:'[^\W\d_]+)?)\.\s*(?:</i>)?\s*$", raw)
        if mend and mend.group(1).lower() in NO_END_WORDS:
            issues.append(dict(cue=cue["n"], line_no=ln, category="interrupt", severity="check", text=raw,
                               detail=f"Line ends '{mend.group(1)}.' – probably a cut-off line with the "
                                      "interruption dash read as a full stop."))

        for t in re.findall(r"<\s*(/?)\s*([a-zA-Z]+)[^>]*>", raw):
            tag_open[t[1].lower()] += -1 if t[0] else 1
        for a, b in ("[]", "()"):
            brackets[a] += vis.count(a) - vis.count(b)

    for tag, bal in tag_open.items():
        if bal != 0:
            issues.append(dict(cue=cue["n"], line_no=cue["text_lines"][0][0] if cue["text_lines"] else cue["ts_line_no"],
                               category="tags", severity="error", text=" / ".join(t for _, t in cue["text_lines"]),
                               detail=f"Unbalanced <{tag}> tag ({'unclosed' if bal > 0 else 'extra closing'})."))
    sdh_brackets(cue, issues)


def sdh_brackets(cue, issues):
    """Repair half-bracketed or mismatched SDH: 'sighs]' -> '[sighs]', '[sighs' -> '[sighs]',
    '(sighs]' -> '[sighs]', '[sighs|' -> '[sighs]'."""
    for ln, raw in cue["text_lines"]:
        vis = visible(raw)
        for m in re.finditer(r"\[([^\[\]()|]+)\|", vis):  # [sighs| -> [sighs]
            issues.append(dict(cue=cue["n"], line_no=ln, category="sdh", severity="error", text=raw,
                               detail="Pipe read for a closing bracket.",
                               **fix_fields(raw, make_edit(raw, m.end() - 1, m.end(), "]"))))
        for m in re.finditer(r"\(([^\[\]()]+)\]|\[([^\[\]()]+)\)", vis):  # (sighs] / [sighs)
            inner = m.group(1) if m.group(1) is not None else m.group(2)
            issues.append(dict(cue=cue["n"], line_no=ln, category="sdh", severity="error", text=raw,
                               detail="Mismatched SDH brackets.",
                               **fix_fields(raw, make_edit(raw, m.start(), m.end(), "[" + inner + "]"))))
    def repaired(v):
        if "]" not in v:  # a lone "[" standing in for I is an OCR slip, not SDH
            v = re.sub(r"(?:^|(?<=[\s\"'(-]))\[(?=\s+[a-z]|['’](?:m|ll|d|ve)\b|(?:t|f|n|s)\b|t['’]s\b)", "I", v)
        v = re.sub(r"\[([^\[\]()|]+)\|", r"[\1]", v)
        return re.sub(r"\(([^\[\]()]+)\]|\[([^\[\]()]+)\)", lambda m: "[" + (m.group(1) or m.group(2)) + "]", v)
    lines = [(ln, raw, repaired(visible(raw))) for ln, raw in cue["text_lines"] if raw.strip()]
    for op, cl in (("[", "]"), ("(", ")")):
        bal = sum(v.count(op) - v.count(cl) for _, _, v in lines)
        if bal < 0:  # closing without opening: 'sighs]' -> '[sighs]'
            for ln, raw, v in lines:
                depth, pos = 0, None
                for k, ch in enumerate(v):
                    if ch == op:
                        depth += 1
                    elif ch == cl:
                        if depth == 0:
                            pos = k
                            break
                        depth -= 1
                if pos is not None:
                    pre = re.match(r"^\s*(?:[-–—]\s*)?(?:♪\s*)?", v).end()
                    start = max(pre, max(v.rfind(x, 0, pos) for x in (". ", "! ", "? ", "  ")) + 2
                                if any(v.rfind(x, 0, pos) >= 0 for x in (". ", "! ", "? ")) else pre)
                    issues.append(dict(cue=cue["n"], line_no=ln, category="sdh", severity="error", text=raw,
                                       detail=f"SDH text has '{cl}' but no '{op}'.",
                                       **fix_fields(raw, make_edit(raw, start, start, op))))
                    break
        elif bal > 0:  # opening without closing: '[sighs' -> '[sighs]'
            for ln, raw, v in reversed(lines):
                k = v.rfind(op)
                if k >= 0 and cl not in v[k:]:
                    end = len(v.rstrip())
                    issues.append(dict(cue=cue["n"], line_no=ln, category="sdh", severity="error", text=raw,
                                       detail=f"SDH text has '{op}' but no '{cl}'.",
                                       **fix_fields(raw, make_edit(raw, end, end, cl))))
                    break


def italic_span_checks(cue, issues):
    """Flag italics that start or stop mid-sentence in a way that doesn't look like emphasis."""
    italic = False
    for ln, raw in cue["text_lines"]:
        segs, pos = [], 0  # [(text, is_italic)]
        for m in re.finditer(r"<(/?)i>", raw, re.I):
            segs.append((TAG_RE.sub("", raw[pos:m.start()]), italic))
            italic = m.group(1) == ""
            pos = m.end()
        segs.append((TAG_RE.sub("", raw[pos:]), italic))
        segs = [(t, it) for t, it in segs if t.strip() and not (it and re.fullmatch(r"\s*[-–—.,!?…]*\s*", t))]
        if len(segs) < 2 or len({it for _, it in segs}) < 2:
            continue
        total = sum(len(t.split()) for t, _ in segs)
        for i in range(len(segs) - 1):
            left, right = segs[i][0], segs[i + 1][0]
            if left.rstrip()[-1:] in '.!?,;:"\'’)]—–-…♪#' or right.lstrip()[:1] in '"\'‘“([—–-♪.,!?;:…':
                continue  # boundary at punctuation: plausible change of speaker/voice
            if left[-1:].isalpha() and right[:1].isalpha():
                continue  # tag inside a word: reported by the tag patterns
            ital = segs[i] if segs[i][1] else segs[i + 1]
            words = len(ital[0].split())
            edge = (i == 0 and segs[0][1]) or (i + 1 == len(segs) - 1 and segs[-1][1])
            if words <= 3:
                continue  # short span: emphasis is plausible
            sev = "likely" if words > 3 else "check"
            issues.append(dict(cue=cue["n"], line_no=ln, category="italics", severity=sev, text=raw,
                               detail=f"Italics {'stop' if segs[i][1] else 'start'} mid-sentence "
                                      f"({words} of {total} words italic); looks like OCR rather than emphasis."))


def italic_line_merge(cue, issues):
    """'<i>This message carries</i>' / '<i>a high price</i>' -> one span across the line break."""
    tl = [(ln, t) for ln, t in cue["text_lines"] if t.strip()]
    for (ln1, t1), (ln2, t2) in zip(tl, tl[1:]):
        m1 = re.search(r"</i>\s*$", t1)
        m2 = re.match(r"^\s*<i>", t2)
        if m1 and m2:
            for ln, t, a, b in ((ln1, t1, m1.start(), len(t1)), (ln2, t2, 0, m2.end())):
                issues.append(dict(cue=cue["n"], line_no=ln, category="tags", severity="error", text=t,
                                   detail="Italic tag closed and reopened across a line break; one span covers both lines.",
                                   **fix_fields(t, make_edit(t, a, b, "", on_raw=True))))


def italic_gap_checks(cue, issues):
    """A short roman word sandwiched between italic spans: OCR missed the italics on it."""
    joined = "\n".join(re.sub(r"<i>\s*[-–—]\s*</i>", "-", t) for _, t in cue["text_lines"])
    for m in re.finditer(r"</i>([ \n]*)([^<>\n ]{1,8}(?: [^<>\n ]{1,8})?)([ \n]*)<i>", joined, re.I):
        gap = m.group(2)
        if re.fullmatch(r"[-–—♪#\[\](){}.,!?;:…\"']+", gap):
            continue
        # which file line holds the gap
        pos, ln = m.start(2), None
        for lno, t in cue["text_lines"]:
            if pos <= len(t):
                ln = lno
                break
            pos -= len(t) + 1
        issues.append(dict(cue=cue["n"], line_no=ln, category="italics", severity="likely",
                           text=" / ".join(t for _, t in cue["text_lines"]),
                           detail=f"Roman '{gap}' between italic spans; OCR probably missed its italics.",
                           edit=None, proposed=None))


def file_format_checks(cues, enc, crlf, text, issues):
    if enc == "utf-16":
        issues.append(dict(cue=None, line_no=1, category="encoding", severity="error", text="",
                           detail="UTF-16 file; Plex needs UTF-8. Normalise to UTF-8 without BOM.", fix="normalize"))
    elif enc != TARGET_ENCODING or not crlf:
        have = f"{'UTF-8 with BOM' if enc == 'utf-8-sig' else 'UTF-8 without BOM' if enc == 'utf-8' else enc}, {'CRLF' if crlf else 'LF'}"
        issues.append(dict(cue=None, line_no=1, category="encoding", severity="error", text="",
                           detail=f"File is {have}; house style is UTF-8 without BOM, CRLF.", fix="normalize"))
    if not text.endswith(("\n", "\r")):
        issues.append(dict(cue=None, line_no=None, category="encoding", severity="error", text="",
                           detail="No newline at end of file.", fix="normalize"))
    for c in cues:
        body = " ".join(visible(t) for _, t in c["text_lines"])
        if FANSUB_STRONG.search(body):
            sev = "likely"
        elif FANSUB_WEAK.search(body):
            sev = "check"
        else:
            continue
        issues.append(dict(cue=c["n"], line_no=c["ts_line_no"], category="credits", severity=sev, text=body,
                           detail="Fansub/ripper credit; delete." if sev == "likely" else
                                  "Credit line; delete if fansub, keep if it's the disc's official credit.",
                           fix="delete_cue"))



def strip_accents(w):
    return "".join(ch for ch in unicodedata.normalize("NFD", w) if not unicodedata.combining(ch))


def us_key(w):
    """Collapse British/American spelling pairs onto one key."""
    w = w.lower()
    if len(w) < 5:
        return w  # our/or, for/four
    for a, b in ((r"our(s|ed|ing|ite|ites|ful|less|able|er|ers)?$", r"or\1"),
                 (r"is(e|es|ed|ing|ation|ations|er|ers)$", r"iz\1"),
                 (r"ys(e|es|ed|ing)$", r"yz\1"), (r"tre(s)?$", r"ter\1"),
                 (r"ll(ed|ing|er|ers)$", r"l\1"), (r"ogue(s)?$", r"og\1"), (r"ence$", "ense")):
        w = re.sub(a, b, w)
    return w


CONFUSABLE = {frozenset(p) for p in ("ce", "ao", "eo", "il", "hb", "nu", "nh", "ft", "uv", "vy", "gq",
                                      "cd", "li", "tl", "ij", "co")}


def ocr_variant(a, b):
    """True if a and b differ by one OCR-plausible change (confusable letter, doubled letter, rn/m)."""
    a, b = a.lower(), b.lower()
    if len(a) == len(b):
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        return len(diff) == 1 and frozenset(diff[0]) in CONFUSABLE
    if len(a) > len(b):
        a, b = b, a
    if len(b) - len(a) == 1:
        for i in range(len(b)):
            if b[:i] + b[i + 1:] == a:
                return 0 < i and b[i] == b[i - 1]  # doubled letter (Hollis/Holis)
    return b.replace("rn", "m") == a or b.replace("cl", "d") == a


def edit1(a, b):
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    return any(b[:i] + b[i + 1:] == a for i in range(len(b)))


SE_SKIP = {"d'you", "D'you", "d’you", "D’you", "dumbass", "Dumbass"}  # colloquial on purpose, not OCR errors
SE_LIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references",
                            "se_eng_OCRFixReplaceList.xml")
_SE = None


def load_se_list():
    """Subtitle Edit's English OCR fix list (MIT). Returns dict of section -> data."""
    global _SE
    if _SE is not None:
        return _SE
    _SE = {"words": {}, "partial_words": [], "lines_partial": [], "begin": [], "end": [], "whole": {}, "regex": []}
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(SE_LIST_FILE).getroot()
    except Exception:
        return _SE
    def get(tag):
        node = root.find(tag)
        return [] if node is None else [(i.get("from"), i.get("to")) for i in node if i.get("from")
                                        and not any(k.lower() in i.get("from").lower() for k in SE_SKIP)]
    _SE["words"] = {k: v for k, v in get("WholeWords") if k not in SE_SKIP}
    _SE["partial_words"] = get("PartialWordsAlways")
    _SE["lines_partial"] = get("PartialLinesAlways") + get("PartialLines")
    _SE["begin"] = get("BeginLines")
    _SE["end"] = get("EndLines")
    _SE["whole"] = dict(get("WholeLines"))
    for i in root.findall("RegularExpressions/RegEx"):
        f, w = i.get("find"), i.get("replaceWith")
        if not f or w is None:
            continue
        f = f.replace(r"\p{Ll}", "[a-zà-öø-ÿ]").replace(r"\p{L}", "[^\\W\\d_]")
        w = re.sub(r"\$(\d)", r"\\g<\1>", w)
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                _SE["regex"].append((re.compile(f), w))
        except re.error:
            pass
    return _SE


def se_list_checks(cue, issues, lex):
    se = load_se_list()
    if not se["words"]:
        return
    for ln, raw in cue["text_lines"]:
        if not raw.strip():
            continue
        v = visible(raw)

        def add(vs, ve, new, frm, to):
            sev = "check" if re.fullmatch(r"[A-Za-z']+", frm.strip()) and lex.known(frm.strip()) else "likely"
            issues.append(dict(cue=cue["n"], line_no=ln, category="ocr", severity=sev, text=raw,
                               detail=f"Known OCR error ({frm.strip()!r} -> {to.strip()!r}, Subtitle Edit list).",
                               token=frm.strip(), **fix_fields(raw, make_edit(raw, vs, ve, new))))
        st = v.strip()
        if st in se["whole"]:
            a = v.index(st)
            add(a, a + len(st), se["whole"][st], st, se["whole"][st])
            continue
        for m in re.finditer(r"\S+", v):
            tok, a = m.group(0), m.start()
            core = tok
            lead = len(core) - len(core.lstrip('"([¿¡-'))
            core = core[lead:]
            core = core.rstrip('.,!?;:")]…')
            for cand, off in ((tok, 0), (core, lead)):
                if cand and cand in se["words"] and se["words"][cand] != cand:
                    add(a + off, a + off + len(cand), se["words"][cand], cand, se["words"][cand])
                    break
            for frm, to in se["partial_words"]:
                k = tok.find(frm)
                if k >= 0:
                    add(a + k, a + k + len(frm), to, frm, to)
        padded = " " + v + " "
        for frm, to in se["lines_partial"]:
            k = padded.find(frm)
            if k < 0:
                continue
            if (not frm[:1].isspace() and k > 0 and padded[k - 1].isalpha()) or \
                    (not frm[-1:].isspace() and padded[k + len(frm):k + len(frm) + 1].isalpha()):
                continue  # a fragment inside a word (girls here, hoofbeats): not this error
            new = to
            vs, ve = k - 1, k - 1 + len(frm)       # span in v (padded has one extra char in front)
            if vs < 0:                              # matched the leading pad space
                vs, new = 0, (new[1:] if new.startswith(" ") else new)
            if ve > len(v):                         # matched the trailing pad space
                ve, new = len(v), (new[:-1] if new.endswith(" ") else new)
            add(vs, ve, new, frm, to)
        lead_ws = len(v) - len(v.lstrip())
        for frm, to in se["begin"]:
            if v.lstrip().startswith(frm):
                add(lead_ws, lead_ws + len(frm), to, frm, to)
                break
        body_end = len(v.rstrip())
        for frm, to in se["end"]:
            if v.rstrip().endswith(frm):
                add(body_end - len(frm), body_end, to, frm, to)
                break
        for rx, w in se["regex"]:
            for m in rx.finditer(v):
                new = m.expand(w)
                if new != m.group(0):
                    add(m.start(), m.end(), new, m.group(0), new)


PLACES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "places.txt")


def load_places():
    global _PLACES
    if _PLACES is not None:
        return _PLACES
    _PLACES = _load_places()
    return _PLACES


def _load_places():
    single, multi_first, multi_last = {}, defaultdict(list), defaultdict(list)
    try:
        lines = open(PLACES_FILE, encoding="utf-8").read().splitlines()
    except OSError:
        return single, multi_first, multi_last
    for name in lines:
        name = name.strip()
        if not name or name.startswith("#"):
            continue
        words = re.findall(r"[^\W\d_][^\W\d_'’.-]*(?:['’.-][^\W\d_]+)*", name)
        if len(words) == 1:
            single[strip_accents(words[0]).lower()] = words[0]
        elif words:
            key = [strip_accents(w).lower() for w in words]
            multi_first[key[0]].append((key, words))
            multi_last[key[-1]].append((key, words))
    return single, multi_first, multi_last


COMMON_NOUN_PLACES = {"jersey", "cologne", "china", "turkey", "chile", "java", "champagne", "bordeaux", "cheddar",
                      "oxford", "oxfords", "panama", "bikini", "cayenne", "denim", "sherry", "port", "madeira",
                      "jordan", "mobile", "reading", "bath", "nice", "split", "march", "mars", "victoria", "angola",
                      "cork", "lira", "lima", "derby", "tyre", "providence", "reading", "phoenix", "cardigan", "jersey", "worcester", "tangerine", "suede"}


def place_checks(cues, lex, occ, issues):
    """Misspelled cities, countries, landmarks and districts (one OCR slip from a known place)."""
    single, multi_first, multi_last = load_places()
    if not single:
        return
    fold = lambda w: strip_accents(w).lower()
    by_len = defaultdict(list)
    for k, v in single.items():
        by_len[len(k)].append((k, v))

    def near(tok):
        f = fold(tok)
        if len(f) < 5:
            return None  # short words sit one letter from too many places (Haha/Naha)
        best = None
        for L in (len(f) - 1, len(f), len(f) + 1):
            for k, v in by_len.get(L, []):
                if ocr_variant(f, k):
                    return v, "likely"
                if best is None and len(f) >= 7 and edit1(f, k):
                    best = (v, "check")
        return best

    # single-word places
    for tok, hits in occ.items():
        if len(tok) < 4 or not tok.isalpha():
            continue
        f = fold(tok)
        if f in single:
            common = (lex.spell is not None and lex.spell.word_frequency[tok] >= 5000) or tok in COMMON_NOUN_PLACES
            if tok.islower() and not common:
                sev = "likely" if lex.exact[single[f]] > 0 or not lex.known(tok) else "check"
                for cue_n, ln, raw, _, vs in hits:
                    issues.append(dict(cue=cue_n, line_no=ln, category="places", severity=sev, text=raw,
                                       detail=f"Place name in lowercase.", token=tok,
                                       **fix_fields(raw, make_edit(raw, vs, vs + len(tok), single[f]))))
            continue
        if not tok[:1].isupper() or lex.known(tok.lower()):
            continue
        if all(re.match(r"\s+(?:Road|Rd|Street|St|Avenue|Ave|Lane|Boulevard|Blvd|Square|Bridge|Station|District|"
                        r"Village|Temple|Lu|Jie|Dao|Park|Hotel|Building|Mansion|Market|Pier|Hill|River|Laundry)\b",
                        visible(raw)[vs + len(tok):]) for _, _, raw, _, vs in hits):
            continue  # a street or business name, not the city it resembles
        if all(re.search(r"\b[A-Z][a-z]+\s+$", visible(raw)[:vs]) for _, _, raw, _, vs in hits):
            continue  # after another capitalised word: a person's full name (Flo Spector), not a place
        hit = near(tok)
        if not hit:
            continue
        place, sev = hit
        if lex.exact[place] > 0:
            sev = "likely"  # the correct spelling is used elsewhere in this file
        elif len(hits) >= 3:
            continue  # a consistently spelled name that merely resembles a place
        if tok.isupper():
            place = place.upper()
        for cue_n, ln, raw, _, vs in hits:
            issues.append(dict(cue=cue_n, line_no=ln, category="places", severity=sev, text=raw,
                               detail=f"Probably a misspelling of the place name '{place}'.", token=tok,
                               **fix_fields(raw, make_edit(raw, vs, vs + len(tok), place))))

    # multi-word places: every word but one matches exactly (Hong Kang -> Hong Kong)
    for c in cues:
        for ln, raw in c["text_lines"]:
            v = visible(raw)
            toks = [(m.group(0), m.start()) for m in re.finditer(r"[^\W\d_]+(?:['’][^\W\d_]+)*", v)]
            done = set()
            for i, (t, _) in enumerate(toks):
                for index, anchor in ((multi_first, 0), (multi_last, -1)):
                    for key, words in index.get(fold(t), []):
                        n = len(key)
                        start = i if anchor == 0 else i - n + 1
                        if start < 0 or start + n > len(toks) or (start, tuple(key)) in done:
                            continue
                        window = toks[start:start + n]
                        diffs = [j for j in range(n) if fold(window[j][0]) != key[j]]
                        if len(diffs) != 1:
                            continue
                        j = diffs[0]
                        wt, wpos = window[j]
                        if not wt[:1].isupper() or len(key[j]) < 3:
                            continue
                        if not (ocr_variant(fold(wt), key[j]) or (len(key[j]) >= 4 and edit1(fold(wt), key[j]))):
                            continue
                        if (ln, wpos) in done:
                            continue
                        done.add((start, tuple(key)))
                        done.add((ln, wpos))
                        fixed = words[j].upper() if wt.isupper() else words[j]
                        issues.append(dict(cue=c["n"], line_no=ln, category="places", severity="likely", text=raw,
                                           detail=f"Probably a misspelling of the place name '{' '.join(words)}'.",
                                           token=wt, **fix_fields(raw, make_edit(raw, wpos, wpos + len(wt), fixed))))


def file_level_checks(cues, lex, issues):
    """Per-file consistency. Adds per-occurrence issues and returns a summary dict."""
    occ = defaultdict(list)  # token(original case) -> [(cue, line_no, raw, sentence_start)]
    tally = defaultdict(Counter)
    where = defaultdict(lambda: defaultdict(list))

    def note(key, form, cue_n):
        tally[key][form] += 1
        where[key][form].append(cue_n)

    abbr_hits = []  # (abbr, has_dot, cue, ln, raw)
    variant_hits = defaultdict(list)  # label -> [(form, text, cue, ln, raw, before)]
    interrupts = []  # (form, cue, ln, raw)
    for c in cues:
        for k, (ln, raw) in enumerate(c["text_lines"], 1):
            v = visible(raw)
            for tm in TOKEN_RE.finditer(v):
                tok = tm.group(0).rstrip("'’")
                before = v[:tm.start()].rstrip()
                starts = (k == 1 and not before) or (before.endswith((".", "!", "?", "-", "♪"))
                                                      and not ABBREV_BEFORE_DOT.search(before))
                occ[tok].append((c["n"], ln, raw, starts, tm.start()))
            for label, forms in VARIANT_GROUPS:
                for form, rx in forms:
                    for vm in re.finditer(rx, v):
                        variant_hits[label].append((form, vm.group(0), c["n"], ln, raw, v[:vm.start()], vm.start(), vm.end()))
            mi = INTERRUPT_RE.search(raw)
            if mi:
                form = ("space + " if mi.group(1) else "") + mi.group(2)
                note("interruption dash", form, c["n"])
                interrupts.append((form, mi.group(2), c["n"], ln, raw))
            for a in ABBR_CONTRACTION + ABBR_TRUNCATION:
                for am in re.finditer(rf"\b{a}(\.?)(?=\s+[A-Z])", v):
                    abbr_hits.append((a, bool(am.group(1)), c["n"], ln, raw, am.start(), am.end()))

    def add(tok_occ, sev, detail, find, repl, cat="consistency"):
        for cue_n, ln, raw, _, vs in tok_occ:
            edit = make_edit(raw, vs, vs + len(find), repl) if (repl and find) else None
            issues.append(dict(cue=cue_n, line_no=ln, category=cat, severity=sev, text=raw,
                               detail=detail, token=find, **fix_fields(raw, edit)))

    summary = {}

    def convert(src, major, before):
        """Render the majority form in the case/position of src."""
        if major in ALWAYS_UPPER:
            return major
        if len(src) > 1 and src.isupper() and src not in ALWAYS_UPPER:
            return major.upper()
        start = not before.strip() or before.rstrip()[-1:] in ".!?-♪\"" or src[:1].isupper() and src not in ALWAYS_UPPER
        if src in ALWAYS_UPPER:
            start = not before.strip() or before.rstrip()[-1:] in ".!?-♪\""
        return major[:1].upper() + major[1:] if start else major

    def majority(label, hits, key=lambda h: h[0]):
        cnt = Counter(key(h) for h in hits)
        if len(cnt) < 2:
            return
        best = max(cnt.values())
        tied = [f_ for f_, v in cnt.items() if v == best]
        # majority wins; on a tie, the form used first in the file wins
        top = tied[0] if len(tied) == 1 else min((h for h in hits if key(h) in tied), key=lambda h: (h[2], h[6]))[0]
        n1 = best
        summary[label] = {"counts": dict(cnt), "majority": top, "tie_broken_by_first_use": len(tied) > 1}
        for h in hits:
            form, text, cue_n, ln, raw, before, vs, ve = h
            if key(h) == top:
                continue
            if False:
                pass
            else:
                new = convert(text, top, before)
                why = (f"{label}: file majority is '{top}' ({n1} vs {cnt[key(h)]})." if len(tied) == 1 else
                       f"{label}: tie, so the form used first in the file ('{top}') wins.")
                issues.append(dict(cue=cue_n, line_no=ln, category="consistency", severity="likely", text=raw,
                                   detail=why,
                                   token=text, **fix_fields(raw, make_edit(raw, vs, ve, new))))


    # Accents: OCR drops them far more often than it invents them.
    lower_forms = defaultdict(set)
    for tok in occ:
        lower_forms[strip_accents(tok.lower())].add(tok.lower())
    for tok, hits in occ.items():
        poss = re.search(r"['’]s$", tok)
        tok_base = tok[:poss.start()] if poss else tok
        low = tok_base.lower()
        if low in ACCENTS_STRONG and ACCENTS_STRONG[low]:
            add(hits, "likely", f"Missing accent: {ACCENTS_STRONG[low]}.", tok_base, match_case(tok_base, ACCENTS_STRONG[low]))
        elif low in ACCENTS_SOFT and ACCENTS_SOFT[low] in lower_forms[low]:
            add(hits, "likely", f"File also uses '{ACCENTS_SOFT[low]}'; accent probably lost here.",
                tok, match_case(tok, ACCENTS_SOFT[low]))
        elif len(low) >= 3 and low.isascii() and any(not f.isascii() and strip_accents(f) == low for f in lower_forms[low]) \
                and low not in ACCENTS_SOFT:
            accented = next(f for f in lower_forms[low] if not f.isascii() and strip_accents(f) == low)
            add(hits, "likely", f"Spelled '{accented}' elsewhere in the file; accent probably lost.",
                tok, match_case(tok, accented))

    # Name / proper-noun near-misses: capitalised mid-sentence, not dictionary words.
    names = Counter()
    for tok, hits in occ.items():
        if tok[:1].isupper() and len(tok) >= 4 and not tok.isupper() and tok.isalpha() \
                and (not lex.known(tok.lower()) or any(not h[3] for h in hits) and lex.vocab[tok.lower()] == len(hits)):
            names[tok] = len(hits)
    variants = []
    nl = sorted(names)
    for i, a in enumerate(nl):
        for b in nl[i + 1:]:
            if ocr_variant(a, b) and min(len(a), len(b)) >= 4 \
                    and strip_accents(a) != strip_accents(b):  # accent pairs handled above
                variants.append({a: names[a], b: names[b]})
                minor, major = (a, b) if names[a] < names[b] else (b, a)
                if names[minor] > 2 or names[major] < 3:
                    continue  # consistent spellings: two different names (Fung/Tung, Chan/Char)
                if names[minor] != names[major]:
                    add(occ[minor], "likely", f"Name spelled '{major}' {names[major]}x elsewhere.", minor, major)
                else:
                    add(occ[a] + occ[b], "check", f"Name variants {a} / {b} used equally; pick one.", None, None)
    if variants:
        summary["name variants"] = variants

    # Hyphenation variants: e-mail / email
    hyph = []
    hy_counts = Counter()
    for c in cues:
        for ln, raw in c["text_lines"]:
            for h in re.findall(r"\b[A-Za-z]+-[A-Za-z]+\b", visible(raw)):
                hy_counts[h.lower()] += 1
    vocab_lower = Counter()
    for tok, hits in occ.items():
        vocab_lower[tok.lower()] += len(hits)
    for h, n in hy_counts.items():
        j = h.replace("-", "")
        if not all(len(x) >= 2 and lex.known(x) for x in h.split("-")):
            continue  # chang-e, A-U-S-T-E-R-I-T-Y
        if vocab_lower[j]:
            hyph.append({h: n, j: vocab_lower[j]})
    hy_hits = []
    for pair in hyph:
        (h, _), (j, _) = list(pair.items())
        for tok_lower in (h, j):
            for c in cues:
                for ln, raw in c["text_lines"]:
                    v = visible(raw)
                    for m in re.finditer(r"(?i)\b" + re.escape(tok_lower) + r"\b", v):
                        hy_hits.append((tok_lower, m.group(0), c["n"], ln, raw, v[:m.start()], m.start(), m.end(), j))
    for j in {x[-1] for x in hy_hits}:
        majority(f"hyphenation ({j})", [x[:-1] for x in hy_hits if x[-1] == j])

    # British / American mixing
    groups = defaultdict(Counter)
    for tok, hits in occ.items():
        if tok.isalpha():
            groups[us_key(tok)][tok.lower()] += len(hits)
    for key, g in groups.items():
        if len(g) < 2:
            continue
        sp_hits = []
        for c in cues:
            for ln, raw in c["text_lines"]:
                v = visible(raw)
                for m in TOKEN_RE.finditer(v):
                    if m.group(0).lower() in g:
                        sp_hits.append((m.group(0).lower(), m.group(0), c["n"], ln, raw, v[:m.start()], m.start(), m.end()))
        majority(f"spelling ({'/'.join(sorted(g))})", sp_hits)

    for label, hits in variant_hits.items():
        majority(label, hits)

    # Abbreviation full stops (American vs Hart's rules)
    if abbr_hits:
        con = [h for h in abbr_hits if h[0] in ABBR_CONTRACTION]
        dotted = sum(h[1] for h in con)
        style = "American (Mr.)" if dotted * 2 >= len(con) else "British/Hart's rules (Mr)"
        summary["abbreviations"] = {"style": style if con else "n/a",
                                    "contractions with stop": dotted, "contractions without stop": len(con) - dotted,
                                    "truncations": Counter(f"{h[0]}{'.' if h[1] else ''}" for h in abbr_hits
                                                           if h[0] in ABBR_TRUNCATION)}
        want_dot = dotted * 2 >= len(con) if con else True
        for a, has, cue_n, ln, raw, vs, ve in abbr_hits:
            if a in ABBR_CONTRACTION and has != want_dot:
                issues.append(dict(cue=cue_n, line_no=ln, category="consistency", severity="likely", text=raw,
                                   detail=f"File majority style is {style}.", token=a,
                                   **fix_fields(raw, make_edit(raw, vs, ve, a + ("." if want_dot else "")))))
            if a in ABBR_TRUNCATION and not has:
                issues.append(dict(cue=cue_n, line_no=ln, category="consistency", severity="check", text=raw,
                                   detail=f"'{a}' is a truncation; takes a full stop in both American style "
                                          "and Hart's rules.", token=a,
                                   **fix_fields(raw, make_edit(raw, vs, ve, a + "."))))

    # "Al" (the name) read as "AI": capital I and lowercase l look the same in many subtitle fonts
    al_hits = [(t, h) for t in ("AI", "Al") for h in occ.get(t, [])]
    n_ai, n_al = len(occ.get("AI", [])), len(occ.get("Al", []))
    if n_ai and n_al:
        major, minor = ("Al", "AI") if n_al >= n_ai else ("AI", "Al")
        for cue_n, ln, raw, _, vs in occ.get(minor, []):
            issues.append(dict(cue=cue_n, line_no=ln, category="ocr", severity="likely", text=raw,
                               detail=f"'{minor}' here but '{major}' {max(n_ai, n_al)}x elsewhere; OCR confuses I and l.",
                               token=minor, **fix_fields(raw, make_edit(raw, vs, vs + 2, major))))
    elif n_ai:
        techy = any(w in occ for w in ("computer", "computers", "artificial", "robot", "robots", "android",
                                       "androids", "machine", "machines", "cyborg", "program", "programmed"))
        for cue_n, ln, raw, _, vs in occ["AI"]:
            v = visible(raw)
            if not re.search(r"[a-z]", v):
                continue  # an ALL-CAPS credit or caption: AI may be a name in its own right (Ai Matsubara)
            before, after = v[:vs].rstrip(), v[vs + 2:]
            name_like = bool(re.search(r"(?:\b(?:Uncle|Mr\.?|Big|Little|Hey|Hi|Hello|Thanks|Bye|Dear|Old)|^\s*-?)$", before)
                             and re.match(r"^\s*[,!?.]|^\s*$", after)) or \
                bool(re.search(r"\b(?:Uncle|Mr\.?|Dear|Hey|Hi)$", before))
            if name_like or not techy:
                issues.append(dict(cue=cue_n, line_no=ln, category="ocr", severity="likely" if name_like else "check",
                                   text=raw, detail="'AI' may be the name Al (OCR reads l as I).", token="AI",
                                   **fix_fields(raw, make_edit(raw, vs, vs + 2, "Al"))))

    place_checks(cues, lex, occ, issues)

    # Case at cue and speaker starts; comma ending a cue before a new speaker
    def first_letter(v):
        m = re.match(r"^\s*(?:[-–—]\s*)?(?:♪\s*)?[\"'¿¡]?\s*", v)
        return m.end()
    for ci, c in enumerate(cues):
        tl = [(ln, t) for ln, t in c["text_lines"] if t.strip()]
        if not tl:
            continue
        prev = None
        if ci:
            pl = [t for _, t in cues[ci - 1]["text_lines"] if t.strip()]
            prev = visible(pl[-1]).rstrip() if pl else None
        for k, (ln, t) in enumerate(tl):
            v = visible(t)
            pos = first_letter(v)
            ch = v[pos:pos + 1]
            if not ch.islower():
                continue
            new_speaker = (bool(re.match(r"^\s*[-–—]\s", v)) or bool(re.match(r"^\s*[-–—](?=\S)", v))) \
                and not re.search(r"\s[-–—]\s*$", v)
            after_stop = k == 0 and prev is not None and re.search(r"(?<!\.)[.!?]['\"]?$", prev) \
                and not prev.endswith("...")
            if re.match(r"(?:i|l)(?:\b|['’])|l[bcdfghjkmnpqrstvwxz]", v[pos:]):
                continue  # lowercase pronoun i is reported by the OCR checks
            if re.match(r"^[^\[(]*[\])]", v[pos:]):
                continue  # SDH descriptor whose opening bracket was lost (fixed by the bracket rule)
            if re.search(r"\s[-–—]\s*$", v):
                continue  # paired dashes around an aside continue the previous sentence
            if new_speaker or after_stop:
                issues.append(dict(cue=c["n"], line_no=ln, category="case", severity="likely", text=t,
                                   detail="Sentence starts lowercase." if after_stop and not new_speaker
                                   else "New speaker starts lowercase.",
                                   **fix_fields(t, make_edit(t, pos, pos + 1, ch.upper()))))
        last_ln, last = tl[-1]
        if ci + 1 < len(cues):
            nl = [t for _, t in cues[ci + 1]["text_lines"] if t.strip()]
            if nl and re.match(r"^\s*(?:<i>)?\s*[-–—]\s*\S", nl[0]) and visible(last).rstrip().endswith(","):
                v = visible(last)
                k = len(v.rstrip()) - 1
                issues.append(dict(cue=c["n"], line_no=last_ln, category="punctuation", severity="likely", text=last,
                                   detail="Cue ends with a comma but the next cue starts a new speaker; "
                                          "probably a full stop.",
                                   **fix_fields(last, make_edit(last, k, k + 1, "."))))

    # Full stop missing at the end of a cue (OCR drops the small dot) and unmatched quotes
    def cue_text(c):
        return [(ln, t) for ln, t in c["text_lines"] if t.strip()]
    for ci, c in enumerate(cues):
        tl = cue_text(c)
        if not tl:
            continue
        last_ln, last = tl[-1]
        lv = visible(last).rstrip()
        def unpunctuated(cue):
            t = cue_text(cue)
            return bool(t) and bool(re.search(r"[A-Za-z0-9]$", visible(t[-1][1]).rstrip()))
        song_run = (ci > 0 and unpunctuated(cues[ci - 1])) or (ci + 1 < len(cues) and unpunctuated(cues[ci + 1]))
        last_word = (re.findall(r"[A-Za-z']+", lv) or [""])[-1].lower()
        caps_end = bool(re.search(r"\b[A-Z]{2,}$", lv)) or ":" in lv
        if song_run or lv.isupper() or caps_end or "♪" in lv or last_word in NO_END_WORDS | {
                "on", "in", "at", "to", "for", "with", "of", "from", "by", "about", "into", "like", "as"}:
            pass
        elif ci + 1 < len(cues) and lv and (lv[-1].isalpha() or lv[-1].isdigit()):
            nl = cue_text(cues[ci + 1])
            if nl:
                nv = re.sub(r"^\s*(?:[-–—]\s*)?[\"'¿¡]?\s*", "", visible(nl[0][1]))
                w = re.match(r"[A-Za-z']+", nv)
                if w and w.group(0)[0].isupper() and w.group(0) not in ("I", "I'm", "I'll", "I've", "I'd"):
                    word = w.group(0)
                    starter = word.lower() in SENTENCE_STARTERS or lex.exact[word.lower()] >= 2
                    if starter:
                        k = len(lv)
                        issues.append(dict(cue=c["n"], line_no=last_ln, category="punctuation", severity="likely",
                                           text=last, detail=f"No full stop at the end of the cue, but the next cue "
                                                             f"starts a new sentence ('{word}').",
                                           **fix_fields(last, make_edit(last, k, k, "."))))
        n_q = sum(visible(t).count('"') for _, t in tl)
        if n_q % 2 == 1:
            prev_odd = ci > 0 and sum(visible(t).count('"') for _, t in cue_text(cues[ci - 1])) % 2 == 1
            next_odd = ci + 1 < len(cues) and sum(visible(t).count('"') for _, t in cue_text(cues[ci + 1])) % 2 == 1
            edge_quote = all(
                all(re.match(r'^\s*(?:[-–—]\s*)?(?:♪\s*)?$', v[:k]) or re.match(r'^[.,!?;:…\s]*$', v[k + 1:])
                    for k in [m.start() for m in re.finditer('"', v)])
                for v in (visible(t) for _, t in tl))
            if not prev_odd and not next_odd and not edge_quote:
                issues.append(dict(cue=c["n"], line_no=tl[0][0], category="punctuation", severity="check",
                                   text=" / ".join(t for _, t in tl),
                                   detail="Unmatched quotation mark (no partner in this cue or the next/previous).",
                                   edit=None, proposed=None))

    # Two-speaker cues where line 1 lost its dash ("Want to hear it? / - Yes, sing it.")
    both, second_only = [], []
    for ci, c in enumerate(cues):
        tl = [(ln, t) for ln, t in c["text_lines"] if t.strip()]
        if len(tl) != 2:
            continue
        d = [bool(re.match(r"^\s*(?:<i>)?\s*[-–—]\s*\S", t)) for _, t in tl]
        if d[0] and d[1]:
            both.append(c)
        elif d[1] and not d[0]:
            prev = visible(cues[ci - 1]["text_lines"][-1][1]).strip() if ci and cues[ci - 1]["text_lines"] else "."
            first = visible(tl[0][1]).strip()
            if re.match(r"^[A-Z][A-Z0-9 #.'-]+:", first) or (re.search(r"[A-Z]", first) and not re.search(r"[a-z]", first)) \
                    or re.fullmatch(r"[\[(].*[\])]", first):
                continue  # speaker label, on-screen caption or sound description: not a speaker line
            continues = first[:1].islower() or not re.search(r"[.!?…\"'♪\])]$", prev)
            second_only.append((c, tl[0], continues))
    # Two-speaker cues where BOTH dashes are missing: a question answered by a short reply
    REPLY = re.compile(r"^(?:Yeah|Yes|Yep|Yup|No|Nope|Nah|Okay|OK|Sure|Thanks|Thank you|Bye|Me too|Of course|"
                       r"Not really)(?:[,.!?]|$)")
    if len(both) >= 3:
        for c in cues:
            tl = [(ln, t) for ln, t in c["text_lines"] if t.strip()]
            if len(tl) != 2 or any(re.match(r"^\s*(?:<i>)?\s*[-–—>]", t) for _, t in tl):
                continue
            if any(re.match(r"^\s*(?:<i>)?\s*[A-Z][A-Z0-9 #.'-]+:", visible(t)) for _, t in tl):
                continue  # SDH speaker labels (MAN #2:) identify speakers instead of dashes
            v1, v2 = visible(tl[0][1]).strip(), visible(tl[1][1]).strip()
            if not re.search(r"[.!?…\"]$", v1) or not v2[:1].isupper():
                continue
            if REPLY.match(v2) and not REPLY.match(v1):
                for ln, t in tl:
                    k = len(re.match(r"\s*(?:<i>)?\s*", t).group(0))
                    issues.append(dict(cue=c["n"], line_no=ln, category="dialogue", severity="check", text=t,
                                       detail="Looks like two speakers (question and reply) with no dialogue dashes.",
                                       **fix_fields(t, make_edit(t, k, k, "- ", on_raw=True))))

    if second_only:  # house style: both speakers get a dash, whatever the file's habit
        dash = "- "
        for c, (ln, t), continues in second_only:
            issues.append(dict(cue=c["n"], line_no=ln, category="interrupt", severity="check" if continues else "likely",
                               text=t,
                               detail=f"Two-speaker cue: line 2 has a dialogue dash, line 1 doesn't"
                                      + ("; line 1 may continue the previous cue." if continues else "."),
                               **fix_fields(t, make_edit(t, *(2 * [len(re.match(r"\s*(?:<i>)?\s*", t).group(0))]),
                                                         dash, on_raw=True))))
        if second_only:
            summary["dialogue dash on line 1"] = {"both lines dashed": len(both), "only line 2 dashed": len(second_only)}

    # A single hyphen at the end of a line is rarely a real interruption: usually an OCR'd full stop.
    # Always a judgement call, and never part of the majority vote on interruption style.
    for form, mark, cue_n, ln, raw in interrupts:
        if mark == "-":
            mi = INTERRUPT_RE.search(raw)
            if re.search(r"(?:\.\.\.|…)\s*$", visible(raw[:mi.start()])):
                issues.append(dict(cue=cue_n, line_no=ln, category="punctuation", severity="likely", text=raw,
                                   detail="Stray hyphen after an ellipsis.",
                                   **fix_fields(raw, make_edit(raw, mi.start(), len(raw),
                                                               "</i>" if "</i>" in mi.group(0) else "", on_raw=True))))
                continue
            tail = "." + ("</i>" if "</i>" in mi.group(0) else "")
            issues.append(dict(cue=cue_n, line_no=ln, category="punctuation", severity="check", text=raw,
                               detail="Single hyphen at the end of a line: probably a full stop misread by OCR "
                                      "(could be an interruption).",
                               **fix_fields(raw, make_edit(raw, mi.start(), len(raw), tail, on_raw=True))))
    for f_ in [f_ for f_ in tally["interruption dash"] if f_.endswith("-") and not f_.endswith("--")]:
        del tally["interruption dash"][f_]
    interrupts = [x for x in interrupts if x[1] != "-"]

    # Interruption dash form (spaced or unspaced, -- or em/en dash): the file's majority wins
    if len(tally["interruption dash"]) > 1:
        major = tally["interruption dash"].most_common(1)[0][0]
        mmark = major.replace("space + ", "")
        for form, mark, cue_n, ln, raw in interrupts:
            if form != major:
                mi = INTERRUPT_RE.search(raw)
                tail = (" " if major.startswith("space") else "") + mmark + ("</i>" if "</i>" in mi.group(0) else "")
                issues.append(dict(cue=cue_n, line_no=ln, category="interrupt", severity="likely", text=raw,
                                   detail=f"Interruption written '{form}'; file majority is '{major}'.",
                                   **fix_fields(raw, make_edit(raw, mi.start(), len(raw), tail, on_raw=True))))

    for key, cnt in tally.items():
        if len(cnt) > 1:
            major = cnt.most_common(1)[0][0]
            summary[key] = {"counts": dict(cnt), "majority": major,
                            "minority_cues": {f: sorted(set(where[key][f]))[:50] for f in cnt if f != major}}
    return summary


# --------------------------------------------------------------------------- #
# A sound description: [sighs], (door slams), [music playing], (DOOR SLAMS). Speaker tags like [Koo] or
# [Prosecutor] are not: a file that only names speakers isn't SDH.
SDH_DESC = re.compile(r"[\[(](?:\s*[a-z♪][^\])]{1,40}|[^\])]*?\b[A-Za-z]+ing\b[^\])]*)[\])]")
# a bracketed cue standing alone on its line ([Birds Chirping], [Static]) is a sound cue, not a speaker tag
SDH_ALONE = re.compile(r"^\s*-?\s*(?:♪\s*)?[\[(][^\])]{2,40}[\])]\s*(?:♪\s*)?$")


def sdh_name_check(path, cues, issues):
    """A file that describes sounds throughout (in at least 3% of cues, and in every fifth of the film) is an
    SDH file: its name should carry .sdh (Title (Year).en.sdh.srt). Calibrated on the user's library: every
    file already named .sdh passes; regular subtitles with an occasional bracketed note don't."""
    name = os.path.basename(path)
    if re.search(r"\.(?:sdh|cc|hi|forced)\.", name, re.I):
        return
    n = len(cues) or 1
    hits = [k for k, c in enumerate(cues)
            if any(SDH_DESC.search(visible(t)) or SDH_ALONE.match(visible(t)) for _, t in c["text_lines"])]
    parts = {int(5 * k / n) for k in hits}
    if len(hits) / n >= 0.025 and len(parts) == 5:
        new = re.sub(r"(\.[a-z]{2,3}(?:-[A-Za-z]{2,4})?)?\.srt$", lambda m: (m.group(1) or "") + ".sdh.srt", name, flags=re.I)
        issues.append(dict(cue=None, line_no=None, category="filename", severity="likely", text=name,
                           detail=f"Sound descriptions throughout ({len(hits)} cues), so this is an SDH file; "
                                  f"the name should say so.",
                           fix="rename", new_name=new, edit=None, proposed=new))


def check_file(path):
    """Run every check on one file and return the result dict (no printing). Reusable in batch mode."""
    text, enc = read_file(path)
    cues, issues = parse(text)
    sdh_name_check(path, cues, issues)
    lex = Lexicon(cues)
    for c in cues:
        line_checks(c, issues, lex)
        se_list_checks(c, issues, lex)
        cc_markers(c, issues)
        two_speakers_one_line(c, issues)
        italic_span_checks(c, issues)
        italic_gap_checks(c, issues)
        italic_line_merge(c, issues)
    summary = file_level_checks(cues, lex, issues)
    file_format_checks(cues, enc, "\r\n" in text, text, issues)

    # De-duplicate (same line, same detail, same token)
    # A bracket that opens in one cue and closes in the next isn't missing anything
    opens = {i["cue"] for i in issues if i["detail"].startswith("SDH text has '(' but no ')'")
             or i["detail"].startswith("SDH text has '[' but no ']'")}
    closes = {i["cue"] for i in issues if i["detail"].startswith("SDH text has ')' but no '('")
              or i["detail"].startswith("SDH text has ']' but no '['")}
    paired = {c for c in opens if c + 1 in closes} | {c for c in closes if c - 1 in opens}
    issues = [i for i in issues if not (i["detail"].startswith("SDH text has") and i.get("cue") in paired)]

    # Overlapping edits on one line can't both apply: keep the curated Subtitle Edit fix, then
    # certain fixes before probable ones, then the first proposed.
    def prio(it):
        first = "Subtitle Edit list" in it["detail"] or it["detail"].startswith("Two speakers on one line")
        return (0 if first else 1, SEVERITY_ORDER[it["severity"]])
    taken = defaultdict(list)
    kept = []
    for it in sorted(enumerate(issues), key=lambda x: (prio(x[1]), x[0])):
        e = it[1].get("edit")
        if e:
            spans = taken[it[1].get("line_no")]
            def overlaps(a, b, c, d):
                if a == b or c == d:                # an insertion clashes with anything it touches
                    return c <= a <= d or a <= c <= b
                return a < d and c < b
            clash = any(overlaps(e["start"], e["end"], a, b) for a, b in spans)
            if clash:
                continue
            spans.append((e["start"], e["end"]))
        kept.append(it)
    issues = [it for _, it in sorted(kept, key=lambda x: x[0])]
    fixed_old = defaultdict(list)
    for it in issues:
        if it.get("edit"):
            fixed_old[it.get("line_no")].append(it["edit"]["old"])
    issues = [it for it in issues if it.get("edit") or not it.get("token")
              or not any(it["token"] in o for o in fixed_old[it.get("line_no")])]

    seen, uniq = set(), []
    edits_seen = set()
    for it in issues:
        e = it.get("edit")
        if e:
            ek = (it.get("line_no"), e["start"], e["end"], e["new"])
            if ek in edits_seen:
                continue  # the same change proposed by two rules: keep the first
            edits_seen.add(ek)
        key = (it.get("line_no"), it["detail"], it.get("token"), (it.get("edit") or {}).get("start"))
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    cue_by_n = {c["n"]: c for c in cues}
    for it in uniq:
        c = cue_by_n.get(it.get("cue"))
        it["time"] = ms_to_ts(c["start"]) if c else None
        it["index"] = c["index"] if c else None
    uniq.sort(key=lambda x: (SEVERITY_ORDER[x["severity"]], x.get("cue") or 0, x.get("line_no") or 0))

    crlf = "\r\n" in text
    result = {
        "file": path,
        "encoding": enc,
        "line_endings": "CRLF" if crlf else "LF",
        "cue_count": len(cues),
        "runtime": ms_to_ts(cues[-1]["end"]) if cues else None,
        "dictionary": lex.mode,
        "counts": {
            "by_severity": dict(Counter(i["severity"] for i in uniq)),
            "by_category": dict(Counter(i["category"] for i in uniq)),
        },
        "consistency": summary,
        "issues": uniq,
    }
    result["_cues"] = cues
    return result


def dump(cues, full=False):
    """Compact read-through format: '#12 text / text'. full=True adds timestamps and file line numbers."""
    out = []
    for c in cues:
        if full:
            body = " / ".join(f"L{ln}: {t.strip()}" for ln, t in c["text_lines"] if t.strip())
            out.append(f"#{c['n']} [{ms_to_ts(c['start'])}] {body}")
        else:
            body = " / ".join(t.strip() for _, t in c["text_lines"] if t.strip())
            out.append(f"#{c['n']} {body}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("srt")
    ap.add_argument("--json", help="write full results to this JSON file")
    ap.add_argument("--dump", action="store_true", help="print one cue per line for reading (compact)")
    ap.add_argument("--dump-full", action="store_true", help="dump with timestamps and file line numbers")
    ap.add_argument("--max-print", type=int, default=60, help="issues to print to stdout")
    args = ap.parse_args()

    if args.dump or args.dump_full:
        text, enc = read_file(args.srt)
        cues, _ = parse(text)
        print(dump(cues, full=args.dump_full))
        return

    result = check_file(args.srt)
    result.pop("_cues")
    uniq = result["issues"]
    lex_mode = result["dictionary"]
    cues_n = result["cue_count"]
    enc = result["encoding"]
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)

    print(f"{args.srt}: {cues_n} cues, {enc}, {result['line_endings']}, dictionary: {lex_mode}")
    print("By severity:", result["counts"]["by_severity"])
    print("By category:", result["counts"]["by_category"])
    if result["consistency"]:
        for k, v in result["consistency"].items():
            print(f"Consistency – {k}: {v['counts'] if isinstance(v, dict) and 'counts' in v else v}")
    for it in uniq[: args.max_print]:
        sug = f"  ->  {it['proposed']!r}" if it.get("proposed") is not None else ""
        tok = f" [{it['token']}]" if it.get("token") else ""
        print(f"  {it['severity']:6} #{it.get('cue')} L{it.get('line_no')} {it['category']}: {it['detail']}{tok}"
              f"  | {it['text'][:70]!r}{sug}")
    if len(uniq) > args.max_print:
        print(f"  ... {len(uniq) - args.max_print} more (see --json output)")


if __name__ == "__main__":
    main()
