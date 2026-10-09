#!/usr/bin/env python3
"""
Build the targeted read-through digest for one or more SRT files: what Claude reads instead of every cue.

Usage:
  python digest.py report.md            (uses the files listed in report.state.json)
  python digest.py file1.srt file2.srt  (standalone)

For each file it prints, compactly:
  NAMES  capitalised names used mid-sentence with counts; near-identical spellings marked with ~
  FLAG   every cue the checker flagged for judgement (likely/check) or for an OCR/punctuation fix, with
         one cue of context either side. Pure house-style and structure fixes aren't shown.
  PROBE  40 longer cues (100 when the file's suspect rate is elevated) sampled across the file to judge
         translation quality. If the probe turns up three or more grammar/translation problems, read the
         whole file with `srt_check.py --dump`.
  Files whose initial scan shows unusual damage (garble or suspect rate above the thresholds below)
  get a DEEP DIVE instead: every cue, with the checker's flags marked inline, in one read.
"""
import json
import os
import random
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import srt_check  # noqa: E402
import build_report as B  # noqa: E402

PROBE_SIZE = 40
WIDE_PROBE_SIZE = 100

# Escalation thresholds (percent of cues), calibrated on 16 files read in full. Style-only fixes (French
# spacing, two-dot ellipses, single-speaker dashes, house style) don't count. Rough files scored garble
# 0.7-2.1% or suspect 1.3-3.1%; clean files garble <=0.5%, suspect <=0.7% (1.0% with italic songs).
DEEP_GARBLE = 0.6      # garble rate at or above this -> read every cue
DEEP_SUSPECT = 1.4     # suspect rate at or above this -> read every cue
WIDE_SUSPECT = 1.0     # suspect rate at or above this -> digest with a wider probe

SIGNAL = {"ocr", "places", "punctuation", "case", "grammar", "italics", "sdh", "dialogue"}
STYLE = ("Curly", "Unicode ellipsis", "Dialogue dash wrapped", "Dialogue dash without", "En/em dash",
         "Accent mark used", "Look-alike", "Invisible", "Accented letters stored", "No space after music",
         "No space before music", "Padding inside SDH")
GARBLE = ("Unknown word", "Unexpected character", "Stray apostrophe", "Known OCR error", "Pipe", "Digit inside",
          "Zero read", "Letter O inside", "Lowercase l read", "Capital I", "Possible OCR confusion",
          "Possible real-word", "Two dots", "Doubled", "Four or more")


def scores(issues, n_cues):
    """Suspect rate: cues with signs of OCR/translation damage (house-style fixes excluded).
    Garble rate: cues with signs of garbled OCR specifically. Both in percent of cues."""
    n = max(n_cues, 1)

    def style_only(i):
        # consistent style habits, not damage: house-style fixes, French spacing (" !"), two-dot ellipses
        e = i.get("edit") or {}
        old_, new_ = e.get("old") or "", e.get("new") or ""
        # a space added/removed next to punctuation (French spacing "dreaming !") is style; a space inside
        # or between words ("bluff in'", "sniff ting", "ofthe") is OCR damage and still counts
        spacing_only = bool(e) and re.sub(r"\s", "", old_) == re.sub(r"\s", "", new_) and not any(
            re.search(r"[A-Za-z']\s+[A-Za-z']", x) for x in (old_, new_)) and not re.fullmatch(r"[A-Za-z']+", old_ + new_)
        return i["detail"].startswith(STYLE + ("Two dots", "Four or more", "Space before", "Dialogue dash on a")) \
            or spacing_only
    sus = {i["cue"] for i in issues if i.get("cue") and i["category"] in SIGNAL and not style_only(i)}
    gar = {i["cue"] for i in issues if i.get("cue") and i["detail"].startswith(GARBLE) and not style_only(i)}
    return round(100 * len(sus) / n, 1), round(100 * len(gar) / n, 1)


def lev(a, b):
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def names_line(cues, lex):
    cnt = Counter()
    for c in cues:
        for _, t in c["text_lines"]:
            v = srt_check.visible(t)
            for m in re.finditer(r"(?<=[a-z,;] )([A-Z][a-z]+(?: [A-Z][a-z]+|-[A-Za-z][a-z]+)?)", v):
                w = m.group(1)
                if not lex.known(w.split()[0].split("-")[0].lower()):
                    cnt[w] += 1
    if not cnt:
        return "NAMES: (none)"
    names = [n for n, _ in cnt.most_common(40)]
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if min(len(a), len(b)) >= 4 and lev(a.lower(), b.lower()) <= 2 and a.lower() != b.lower():
                pairs.append(f"{a}~{b}")
    out = "NAMES: " + " ".join(f"{n}({cnt[n]})" for n in names)
    if pairs:
        out += "\nSIMILAR: " + " ".join(pairs)
    return out


SDH_ONLY = re.compile(r"^(?:-\s*)?(?:♪+\s*)?[\[(][^\])]*[\])]\s*$")       # (sighs) / - [door slams] / ♪♪ (guitar)
MUSIC_ONLY = re.compile(r"^(?:-\s*)?♪+\s*$")


def safe_to_skip(text_lines, lex, seen):
    """A cue the deep dive can skip: the checker didn't flag it AND it can't hide a reading-level error.
    Only exact repeats of a cue already shown (choruses, recurring "(sighs)" or "Come on!"), all-caps
    captions/credits, and lines made only of interjections ("Yeah.", "Hmm.", "Uh-huh."). The first time a
    sound description or short line appears it is always shown: "(sniff ting)" and "Lets go!" hide errors."""
    vis = [srt_check.visible(t).strip() for t in text_lines]
    joined = " / ".join(vis)
    if joined in seen:
        return True
    if re.search(r"[A-Z]", joined) and not re.search(r"[a-z]", joined):
        return True
    words = re.findall(r"[A-Za-z']+", joined)
    if words and all(srt_check.INTERJECTION.match(w) or w.lower() in ("yeah", "okay", "ok", "uh-huh", "mm-hmm")
                     for w in words) and not re.search(r"[^A-Za-z'\s.,!?…-]", joined):
        return True
    return False


def digest_file(n, path, issues=None):
    text, _ = srt_check.read_file(path)
    cues, _ = srt_check.parse(text)
    if issues is None:  # standalone use; build_report caches results so the checks run once per batch
        issues = srt_check.check_file(path)["issues"]
    data = {"issues": issues}
    lex = srt_check.Lexicon(cues)
    body = {c["n"]: " / ".join(t.strip() for _, t in c["text_lines"] if t.strip()) for c in cues}
    reasons = {}
    for i in data["issues"]:
        # judgement flags, plus certain OCR/punctuation fixes: stray marks often sit inside garbled lines
        # (`Gaza“.` / `The Games are cut'.!` was "Cazal! / The cables are cut!")
        if B._label(i["detail"]):
            continue  # formatting style (dash style, ellipses, spacing): fixed as a block, nothing to read
        if i.get("cue") and (i["severity"] in ("likely", "check") or i["category"] in ("ocr", "punctuation")):
            r = i["detail"].split(";")[0].split(" (")[0].rstrip(".")
            reasons.setdefault(i["cue"], [])
            if r not in reasons[i["cue"]]:
                reasons[i["cue"]].append(r)
    suspect, garble = scores(data["issues"], len(cues))
    if garble >= DEEP_GARBLE or suspect >= DEEP_SUSPECT:
        why = (f"garble {garble}% >= {DEEP_GARBLE}%" if garble >= DEEP_GARBLE
               else f"suspect {suspect}% >= {DEEP_SUSPECT}%")
        rows, seen, skipped, gap = [], set(), 0, False
        by_n = {c["n"]: c for c in cues}
        for c in sorted(body):
            tl = [t for _, t in by_n[c]["text_lines"] if t.strip()]
            if c not in reasons and safe_to_skip(tl, lex, seen):
                skipped += 1
                gap = True
                continue
            seen.add(" / ".join(srt_check.visible(t).strip() for t in tl))
            if gap:
                rows.append("  …")
                gap = False
            tail = f"   ‹{'; '.join(reasons[c])}›" if c in reasons else ""
            rows.append(f"{'>' if c in reasons else ' '}#{c} {body[c]}{tail}")
        out = [f"== {n}. {os.path.basename(path)} ({len(cues)} cues, {len(reasons)} flagged) "
               f"DEEP DIVE ({why}): every cue except {skipped} safe ones (exact repeats, all-caps captions, "
               f"interjections); flags marked >", names_line(cues, lex), "ALL"] + rows
        return "\n".join(out)
    probe_size = WIDE_PROBE_SIZE if suspect >= WIDE_SUSPECT else PROBE_SIZE
    tier = f"WIDER PROBE (suspect {suspect}%)" if probe_size == WIDE_PROBE_SIZE else f"standard (suspect {suspect}%, garble {garble}%)"
    show = set()
    for c in reasons:
        show |= {c - 1, c, c + 1}
    show = sorted(x for x in show if x in body)
    out = [f"== {n}. {os.path.basename(path)} ({len(cues)} cues, {len(reasons)} flagged) {tier}",
           names_line(cues, lex), "FLAG"]
    prev = None
    for c in show:
        if prev is not None and c != prev + 1:
            out.append("  …")
        mark = ">" if c in reasons else " "
        tail = f"   ‹{'; '.join(reasons[c])}›" if c in reasons else ""
        out.append(f"{mark}#{c} {body[c]}{tail}")
        prev = c
    pool = [c for c in body if c not in show and len(re.findall(r"[A-Za-z']+", body[c])) >= 6]
    rnd = random.Random(os.path.basename(path))
    probe = sorted(rnd.sample(pool, min(probe_size, len(pool))))
    out.append("PROBE")
    out += [f" #{c} {body[c]}" for c in probe]
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    if len(args) == 1 and args[0].endswith(".md"):
        state = json.load(open(os.path.splitext(args[0])[0] + ".state.json", encoding="utf-8"))
        srt_check.load_accepted(state.get("lexicon"))
        cache = state.get("issues", {})
        jobs = [(n, f["path"], cache.get(str(n))) for n, f in enumerate(state["files"], 1) if not f.get("error")]
    else:
        jobs = [(n, p, None) for n, p in enumerate(args, 1)]
    print("\n\n".join(digest_file(n, p, iss) for n, p, iss in jobs))


if __name__ == "__main__":
    main()
