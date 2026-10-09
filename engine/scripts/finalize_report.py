#!/usr/bin/env python3
"""
Apply the read-through's decisions to a draft report and re-render it with fresh IDs.

Usage:
  python finalize_report.py report.md decisions.json

decisions.json (IDs refer to the CURRENT working view, <report>.working.md; cues and lines are cue numbers and 1-based text lines):
{
 "drop":  ["1.6", "1.12"],                          # rows the read-through rejects
 "strip": {"1.3": [1644]},                          # remove these cues from a grouped row
 "edit":  {"2.2": {"lines": {"255": {"1": "It's a tough business, this."}}, "reason": "Confirm: ..."}},
 "accept_words": ["tallywacker", "godbrother", "Biggie"],   # dismissed unknown words -> srt_qa_lexicon.txt
 "add":   [{"file": 2, "category": "OCR", "cue": 73, "lines": {"1": "Squat!"}, "reason": "Confirm: ..."},
           {"file": 2, "category": "OCR", "cue": 368, "lines": {}, "reason": "Garbled; check the video."},
           {"file": 3, "category": "Credits", "cue": 797, "delete_cue": true, "reason": "Track label, not dialogue."}]
}
"lines" maps line-within-cue to the full new text; a value may contain "\\n" only to split two speakers.
For "edit", "lines" is keyed by cue, then line. An empty "lines" adds a flag-only row. Add "call": true to an "add" or "edit" whose fix is a
guess or a judgement the user should make (garbled lines, unclear names): it goes under "Needs your call"
in the report instead of "Corrections". Rows without a fix always go there.
Every cue and line is checked against the file; anything that doesn't exist stops the run.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import srt_check  # noqa: E402
import build_report as B  # noqa: E402


def cue_index(path):
    text, _ = srt_check.read_file(path)
    cues, _ = srt_check.parse(text)
    by_n = {c["n"]: c for c in cues}
    line_to_cue = {ln: c["n"] for c in cues for ln, _ in c["text_lines"]}
    return by_n, line_to_cue


def text_lines(cue):
    return [t for _, t in cue["text_lines"] if t.strip()]


def build_fixes(by_n, cue_n, lines, where):
    if cue_n not in by_n:
        sys.exit(f"{where}: no cue {cue_n}")
    tl = text_lines(by_n[cue_n])
    fixes, before, after = [], list(tl), list(tl)
    for k, new in sorted(lines.items(), key=lambda kv: int(kv[0])):
        k = int(k)
        if not 1 <= k <= len(tl):
            sys.exit(f"{where}: cue {cue_n} has {len(tl)} text line(s), not line {k}")
        fixes.append({"cue": cue_n, "line": k, "replace_line": new})
        after[k - 1] = new
    return fixes, " / ".join(before), " / ".join(a.replace("\n", " / ") for a in after)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    out_md, dec_path = sys.argv[1:]
    state_path = os.path.splitext(out_md)[0] + ".state.json"
    state = json.load(open(state_path, encoding="utf-8"))
    dec = json.load(open(dec_path, encoding="utf-8"))
    files = state["files"]
    multi = len(files) > 1

    def locate(rid):
        f, k = (rid.split(".") if multi else ("1", rid))
        rows = state["rows"].get(f, [])
        if not 1 <= int(k) <= len(rows):
            sys.exit(f"no row {rid}")
        return f, int(k) - 1

    idx = {}

    def index(f):
        if f not in idx:
            idx[f] = cue_index(files[int(f) - 1]["path"])
        return idx[f]

    # edits and strips first (they use current IDs), then drops, then adds
    for rid, e in dec.get("edit", {}).items():
        f, k = locate(rid)
        row = state["rows"][f][k]
        by_n, _ = index(f)
        if "lines" in e:
            fixes, cur, sug = [], [], []
            for cue_n, lines in e["lines"].items():
                fx, c, s_ = build_fixes(by_n, int(cue_n), lines, rid)
                fixes += fx
                cur.append(c)
                sug.append(s_)
            row["fixes"], row["current"], row["suggested"] = fixes, " / ".join(cur), " / ".join(sug)
            row["cues"] = sorted(int(c) for c in e["lines"])
            row["ts"] = srt_check.ms_to_ts(by_n[row["cues"][0]]["start"])
        if "call" in e:
            row["sev"] = "check" if e["call"] else "likely"
        elif "lines" in e:
            row["sev"] = "likely"
        for key in ("reason", "category"):
            if key in e:
                row["cat" if key == "category" else key] = e[key]
    for rid, cues in dec.get("strip", {}).items():
        f, k = locate(rid)
        row = state["rows"][f][k]
        _, line_to_cue = index(f)
        row["fixes"] = [fx for fx in row["fixes"]
                        if line_to_cue.get(fx.get("line_no"), fx.get("cue")) not in set(cues)]
        row["cues"] = [c for c in row["cues"] if c not in set(cues)]
    drop = {locate(r) for r in dec.get("drop", [])}
    drop |= {locate(r) for r, cues in dec.get("strip", {}).items() if not state["rows"][locate(r)[0]][locate(r)[1]]["cues"]}
    for f in list(state["rows"]):
        state["rows"][f] = [r for k, r in enumerate(state["rows"][f]) if (f, k) not in drop]
    for a in dec.get("add", []):
        f = str(a["file"])
        by_n, _ = index(f)
        fixes, cur, sug = build_fixes(by_n, a["cue"], a.get("lines", {}), f"add cue {a['cue']}")
        if a.get("delete_cue"):
            fixes, sug = [{"delete_cue": a["cue"]}], "Delete cue"
        state["rows"].setdefault(f, []).append(dict(
            cat=a["category"], cues=[a["cue"]], ts=srt_check.ms_to_ts(by_n[a["cue"]]["start"]),
            current=cur, suggested=sug if fixes else None, reason=a["reason"], fixes=fixes,
            sev="check" if (a.get("call") or not fixes) else "likely"))

    words = sorted({w.strip().lower() for w in dec.get("accept_words", []) if w.strip()})
    if words:
        lex_path = state.get("lexicon") or os.path.join(os.path.dirname(os.path.abspath(out_md)), "srt_qa_lexicon.txt")
        have = srt_check.load_accepted(lex_path).copy()
        new = [w for w in words if w not in have]
        if new:
            exists = os.path.exists(lex_path)
            with open(lex_path, "a", encoding="utf-8") as fh:
                if not exists:
                    fh.write("# srt-qa accepted words: names, slang and terms that aren't OCR errors.\n"
                             "# Keep this file with srt_qa_ledger.json and upload both with each batch.\n")
                fh.write("\n".join(new) + "\n")
            print(f"Accepted words: +{len(new)} -> {lex_path}")

    items = B.render(state, out_md)
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    t = state.get("totals", {})
    print(f"{t.get('fix', 0)} corrections, {t.get('call', 0)} need a call -> {out_md}")


if __name__ == "__main__":
    main()
