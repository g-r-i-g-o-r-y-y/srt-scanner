#!/usr/bin/env python3
"""
Apply user-approved fixes to an SRT file, writing a NEW file.

Timestamp lines are never modified. Every fix is verified against the original file before it
is applied; anything that doesn't match is skipped and reported, never guessed at.

fixes.json is a list of objects. line_no is always the 1-based line in the ORIGINAL file.

  Checker fixes (copy "line_no" and "edit" from issues.json):
    {"id": "A1", "line_no": 3, "edit": {"start": 0, "end": 4, "old": "lt's", "new": "It's"}}
  Several edits on one line are combined right-to-left, so they never disturb each other.

  Fixes you write yourself (address a line by file line_no, or by "cue" + "line" within the cue):
    {"id": "C9", "cue": 368, "line": 1, "replace_line": "Should have been \"E\", not \"F\"."}
    {"id": "C4", "line_no": 29, "replace_line": "He doesn't have the courage to eat with us."}
    {"id": "C5", "line_no": 31, "find": "the keep", "replace": "they keep"}   (must occur once)
    {"id": "F1", "delete_cue": 610}       (cue number as reported; removes the whole cue)
    {"id": "F2", "delete_line": 880}      (one text line; never an index or timestamp)
    {"id": "F3", "normalize": true}       (UTF-8 without BOM, CRLF; rebuilds the cue structure:
                                           numbered 1-N, one blank line between cues, no blank lines
                                           inside cues, newline at end of file)

Cues are renumbered 1..N whenever a cue is deleted or normalize is set.

Usage:
  python apply_fixes.py input.srt fixes.json output.srt
"""
import json
import re
import sys

TS_RE = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->")


def read(path):
    raw = open(path, "rb").read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252"), "cp1252"


def blocks(lines):
    """Map cue number -> (first line idx, last line idx inclusive), using timestamp positions."""
    ts = [i for i, l in enumerate(lines) if TS_RE.match(l)]
    out = {}
    for n, p in enumerate(ts, 1):
        start = p - 1 if p > 0 and lines[p - 1].strip().isdigit() else p
        nxt = ts[n] if n < len(ts) else len(lines)
        end = (nxt - 2 if nxt < len(lines) and lines[nxt - 1].strip().isdigit() else nxt - 1)
        out[n] = (start, end)
    return out


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    src, fixes_path, dst = sys.argv[1:]
    if src == dst:
        sys.exit("Refusing to overwrite the original; choose a different output path.")
    text, enc = read(src)
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith(("\n", "\r"))
    orig = text.splitlines()
    lines = list(orig)
    fixes = json.load(open(fixes_path, encoding="utf-8"))
    # Fixes may address a line by cue number and line-within-cue instead of file line number:
    #   {"cue": 368, "line": 1, "replace_line": "..."}   (line counts non-blank text lines from 1)
    cue_map0 = blocks(orig)
    for fx in fixes:
        if "cue" in fx and "line" in fx and "line_no" not in fx:
            span = cue_map0.get(fx["cue"])
            if span:
                a, b = span
                ts = next((i for i in range(a, b + 1) if TS_RE.match(orig[i])), None)
                text_idx = [i for i in range(ts + 1, b + 1) if orig[i].strip()] if ts is not None else []
                if 1 <= fx["line"] <= len(text_idx):
                    fx["line_no"] = text_idx[fx["line"] - 1] + 1
    applied, skipped = [], []

    def protected(i):
        return TS_RE.match(orig[i]) or (orig[i].strip().isdigit() and i + 1 < len(orig) and TS_RE.match(orig[i + 1]))

    # 1. exact edits, grouped per line, applied right-to-left on the original text
    by_line = {}
    for fx in fixes:
        if "edit" in fx:
            by_line.setdefault(fx.get("line_no"), []).append(fx)
    for ln, group in by_line.items():
        if not isinstance(ln, int) or not 1 <= ln <= len(orig) or protected(ln - 1):
            skipped += [(fx.get("id"), f"line {ln} is not a text line") for fx in group]
            continue
        cur = orig[ln - 1]
        taken = []
        for fx in sorted(group, key=lambda f: -f["edit"]["start"]):
            e = fx["edit"]
            if cur[e["start"]:e["end"]] != e["old"] or orig[ln - 1][e["start"]:e["end"]] != e["old"]:
                skipped.append((fx.get("id"), f"L{ln}: expected {e['old']!r} at {e['start']}"))
                continue
            if any(not (e["end"] <= s or e["start"] >= t) for s, t in taken) or \
                    any(e["start"] == e["end"] == s for s, t in taken):
                skipped.append((fx.get("id"), f"L{ln}: overlaps another approved edit"))
                continue
            cur = cur[:e["start"]] + e["new"] + cur[e["end"]:]
            taken.append((e["start"], e["end"]))
            applied.append((fx.get("id"), f"L{ln}: {e['old']!r} -> {e['new']!r}"))
        lines[ln - 1] = cur

    # 2. find/replace and replace_line, in order, on the current text
    for fx in fixes:
        fid, ln = fx.get("id"), fx.get("line_no")
        if "replace_line" in fx or "find" in fx:
            if not isinstance(ln, int) or not 1 <= ln <= len(orig) or protected(ln - 1):
                skipped.append((fid, f"line {ln} is not a text line"))
                continue
            cur = lines[ln - 1]
            if "replace_line" in fx:
                lines[ln - 1] = fx["replace_line"]
                applied.append((fid, f"L{ln}: {cur!r} -> {fx['replace_line']!r}"))
            elif cur.count(fx["find"]) == 1:
                lines[ln - 1] = cur.replace(fx["find"], fx["replace"])
                applied.append((fid, f"L{ln}: {cur!r} -> {lines[ln - 1]!r}"))
            else:
                skipped.append((fid, f"L{ln}: {fx['find']!r} found {cur.count(fx['find'])}x in {cur!r}"))

    # 3. deletions
    drop = set()
    cue_deleted = False
    cue_map = blocks(orig)
    for fx in fixes:
        fid = fx.get("id")
        if "delete_cue" in fx:
            n = fx["delete_cue"]
            if n not in cue_map:
                skipped.append((fid, f"no cue {n}"))
                continue
            a, b = cue_map[n]
            drop.update(range(a, b + 1))
            # also drop the blank line(s) that separated it from the next cue
            j = b + 1
            while j < len(orig) and not orig[j].strip():
                drop.add(j)
                j += 1
            cue_deleted = True
            applied.append((fid, f"deleted cue {n}: {' / '.join(lines[i] for i in range(a, b + 1) if i not in (a, a + 1))!r}"))
        elif "delete_line" in fx:
            ln = fx["delete_line"]
            if not 1 <= ln <= len(orig) or protected(ln - 1):
                skipped.append((fid, f"line {ln} is not a text line"))
                continue
            drop.add(ln - 1)
            applied.append((fid, f"deleted L{ln}: {orig[ln - 1]!r}"))
    # a fix may split one line into two (two speakers on one line): expand embedded line breaks
    out = [piece for i, l in enumerate(lines) if i not in drop for piece in l.split("\n")]

    # 4. normalise and renumber
    normalize = any(fx.get("normalize") for fx in fixes)
    if normalize:
        # Rebuild the cue structure from the timestamp lines: number, timestamp, text lines
        # (blank lines inside a cue removed), exactly one blank line between cues.
        ts_pos = [i for i, l in enumerate(out) if TS_RE.match(l)]
        rebuilt = []
        if ts_pos:
            first = ts_pos[0] - 1 if ts_pos[0] > 0 and out[ts_pos[0] - 1].strip().isdigit() else ts_pos[0]
            pre = [l for l in out[:first] if l.strip()]
            rebuilt += pre + ([""] if pre else [])
            for n, p in enumerate(ts_pos, 1):
                nxt = ts_pos[n] if n < len(ts_pos) else len(out)
                end = nxt - 1 if nxt < len(out) and nxt - 1 > p and out[nxt - 1].strip().isdigit() else nxt
                body = [l for l in out[p + 1:end] if l.strip()]
                rebuilt += [str(n), out[p].strip()] + body + [""]
        else:
            rebuilt = [l for l in out]
        while rebuilt and not rebuilt[-1].strip():
            rebuilt.pop()
        out = rebuilt
        enc, newline, trailing = "utf-8", "\r\n", True
        applied.append(("normalize", "UTF-8 without BOM, CRLF, cue structure rebuilt (numbered 1-N, one blank "
                                     "line between cues, no blank lines inside cues), final newline"))
    if cue_deleted or normalize or any(fx.get("renumber") for fx in fixes):
        k = 0
        for i, l in enumerate(out):
            if l.strip().isdigit() and i + 1 < len(out) and TS_RE.match(out[i + 1]):
                k += 1
                out[i] = str(k)

    data = newline.join(out) + (newline if trailing else "")
    with open(dst, "wb") as f:
        f.write(data.encode(enc))

    # Safety: surviving timestamps are untouched (deleted cues excepted)
    kept_ts = [l.strip() for i, l in enumerate(orig) if TS_RE.match(l) and i not in drop]
    new_ts = [l.strip() for l in out if TS_RE.match(l)]
    ok = kept_ts == new_ts
    print(f"Applied {len(applied)}, skipped {len(skipped)}. Timestamps unchanged: {ok}. Output: {enc}, "
          f"{'CRLF' if newline == chr(13) + chr(10) else 'LF'}")
    for fid, msg in applied:
        print(f"  OK   {fid}: {msg}")
    for fid, msg in skipped:
        print(f"  SKIP {fid}: {msg}")
    if not ok:
        sys.exit("ERROR: timestamp lines changed. Output should not be used.")


if __name__ == "__main__":
    main()
