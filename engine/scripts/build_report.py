#!/usr/bin/env python3
"""
Run the checker on one or more SRT files (in one process) and write ONE draft report.

Usage:
  python build_report.py file1.srt [file2.srt ...] --out report.md [--ledger srt_qa_ledger.json]

Writes:
  report.md          one table per file: Category | ID | Cue | Timestamp | Current | Suggested | Reason
  report.items.json  every row's data and exact fixes, keyed by ID ("2.14"), plus the file list

IDs are "<file number>.<row>" when there is more than one file, plain numbers otherwise. Identical fixes
within a file are grouped into one row; several fixes on the same line and category merge into one row.
Files whose fingerprint is already in the ledger (QC'd and unchanged) are skipped.
The read-through edits the report through finalize_report.py, never by hand.
"""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import srt_check  # noqa: E402

LABEL = {"structure": "Structure", "encoding": "Structure", "credits": "Credits", "ocr": "OCR",
         "places": "Places", "spacing": "Spacing", "punctuation": "Punctuation", "case": "Case", "sdh": "SDH",
         "music": "Music notes", "tags": "Tags", "italics": "Italics", "interrupt": "Dialogue",
         "dialogue": "Dialogue", "consistency": "Consistency", "grammar": "Grammar/translation"}
ORDER = ["Structure", "Credits", "OCR", "Places", "Spacing", "Punctuation", "Case", "SDH", "Music notes", "Tags",
         "Italics", "Dialogue", "Consistency", "Grammar/translation"]


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def esc(t):
    return (t or "").replace("\\", "\\\\").replace("|", "\\|").replace("<", "\\<").replace("*", "\\*") \
        .replace("_", "\\_").replace("\n", " / ")


def mark_diff(cur, sug):
    """Escape both sides for the Markdown table and bold the words that differ."""
    import difflib
    if not sug or not cur or cur == sug:
        return esc(cur), esc(sug)
    a, b = re.findall(r"\S+|\s+", cur), re.findall(r"\S+|\s+", sug)
    out_a, out_b = [], []

    def emit(out, toks, bold):
        text = "".join(toks)
        if not bold or not text.strip():
            out.append(esc(text))
            return
        lead = text[:len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        out.append(esc(lead) + "**" + esc(text.strip()) + "**" + esc(trail))
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        emit(out_a, a[i1:i2], op in ("replace", "delete"))
        emit(out_b, b[j1:j2], op in ("replace", "insert"))
    return "".join(out_a), "".join(out_b)


def short_ts(ts):
    return ts.split(",")[0] if ts else "–"


def cue_list(cues):
    cues = sorted(set(c for c in cues if c is not None))
    if not cues:
        return "–"
    head = ", ".join(map(str, cues[:6]))
    return head + (f" (+{len(cues) - 6} more)" if len(cues) > 6 else "")


def rows_for(path, data):
    issues = data["issues"]
    rows = []
    norm = [i for i in issues if i.get("fix") == "normalize"]
    if norm:
        reasons = OrderedDict()
        for i in norm:
            reasons[i["detail"].split(";")[0].split(" (")[0].rstrip(".")] = None
        rows.append(dict(cat="Structure", cues=[], ts=None, current=f"{data['encoding']}, {data['line_endings']}",
                         suggested="UTF-8 without BOM, CRLF, clean cue structure",
                         reason="Normalise: " + "; ".join(reasons), fixes=[{"normalize": True}]))
    for i in issues:
        if i.get("fix") == "rename":
            rows.append(dict(cat="File name", cues=[], ts=None, current=i["text"], suggested=i["new_name"],
                             reason=i["detail"], fixes=[{"rename": i["new_name"]}], sev="likely"))
    doomed = {i["cue"] for i in issues if i.get("fix") == "delete_cue" and i["severity"] == "likely"}
    line_changes = OrderedDict()
    others = []
    for i in issues:
        if i.get("fix") in ("normalize", "rename"):
            continue
        cat = LABEL.get(i["category"], i["category"].title())
        if i.get("cue") in doomed and i.get("fix") != "delete_cue":
            continue
        if i.get("edit"):
            lc = line_changes.setdefault((cat, i["line_no"]), dict(cat=cat, cue=i.get("cue"), ts=i.get("time"),
                                                                   text=i.get("text", ""), edits=[], details=[],
                                                                   sev=set(), line_no=i["line_no"]))
            if all(i["edit"]["end"] <= e["start"] or i["edit"]["start"] >= e["end"] for e in lc["edits"]) \
                    and i["edit"] not in lc["edits"]:
                lc["edits"].append(i["edit"])
            if i["detail"] not in lc["details"]:
                lc["details"].append(i["detail"])
            lc["sev"].add(i["severity"])
        else:
            others.append((cat, i))

    def combined(text, edits):
        for e in sorted(edits, key=lambda e: -e["start"]):
            text = text[:e["start"]] + e["new"] + text[e["end"]:]
        return text

    groups = OrderedDict()
    for lc in line_changes.values():
        sig = (lc["cat"], tuple(lc["details"]), tuple(sorted((e["old"], e["new"]) for e in lc["edits"])))
        reason = "; ".join(d.rstrip(".") for d in lc["details"]) + "."
        if lc["sev"] & {"likely", "check"}:
            reason = "Confirm: " + reason
        fixes = [{"line_no": lc["line_no"], "edit": e} for e in lc["edits"]]
        g = groups.get(sig)
        if g:
            g["cues"].append(lc["cue"])
            g["fixes"] += fixes
        else:
            groups[sig] = dict(cat=lc["cat"], cues=[lc["cue"]], ts=lc["ts"], current=lc["text"],
                               suggested=combined(lc["text"], lc["edits"]), reason=reason, fixes=fixes,
                               sev="check" if "check" in lc["sev"] else "likely" if "likely" in lc["sev"] else "error")
    for cat, i in others:
        if i.get("fix") == "delete_cue":
            key, fixes = ("credit", i["cue"]), [{"delete_cue": i["cue"]}]
            sugg = "Delete cue" if i["severity"] == "likely" else "Delete cue if fansub"
        elif i.get("fix") == "delete_line":
            key, fixes, sugg = ("delline", i["line_no"]), [{"delete_line": i["line_no"]}], "Delete line"
        else:
            key, fixes, sugg = (cat, i["detail"], i.get("line_no")), [], None
        reason = ("Confirm: " if i["severity"] in ("likely", "check") and fixes else "") + i["detail"]
        groups[key] = dict(cat=cat, cues=[i.get("cue")], ts=i.get("time"), current=i.get("text", ""),
                           suggested=sugg, reason=reason, fixes=fixes, sev=i["severity"])
    return rows + list(groups.values())


# House-style / formatting fixes: shown to the user as one summary line per file, approved as a block.
FORMAT_LABELS = [
    ("Normalise", "file format (UTF-8 without BOM, CRLF, cue structure)"),
    ("Curly", "curly quotes"), ("Accent mark used as apostrophe", "curly quotes"),
    ("Unicode ellipsis", "ellipses"), ("Two dots", "ellipses"), ("Four or more dots", "ellipses"),
    ("Dialogue dash without", "dialogue dash spacing"), ("Dialogue dash wrapped", "dialogue dash spacing"),
    ("En/em dash as dialogue", "en/em dash used as dialogue dash (should be a hyphen)"), ("Dialogue dash on a one", "single-speaker dashes"),
    ("Interruption written", "interruption dash style"),
    ("Space before", "space before punctuation"), ("Double space", "double spaces"),
    ("Leading or trailing", "leading or trailing spaces"), ("Non-breaking", "non-breaking spaces"),
    ("Space just inside", "spaces inside tags or quotes"), ("Padding inside SDH", "SDH bracket spacing"),
    ("Disallowed tag", "unsupported tags"), ("Positioning tag", "unsupported tags"), ("ASS override", "unsupported tags"),
    ("Italic tag closed and reopened", "italic tags"), ("Empty italic tag", "italic tags"),
    ("Malformed formatting tag", "italic tags"), ("Italic tag inside a word", "italic tags"),
    ("No space after music", "music note spacing"), ("No space before music", "music note spacing"),
    ("Invisible character", "invisible characters"), ("Look-alike", "look-alike characters"),
    ("Accented letters stored", "accent encoding"), ("Typographic ligature", "ligatures"),
]


def _label(reason):
    r = re.sub(r"^Confirm:\s*", "", reason or "")
    for prefix, label in FORMAT_LABELS:
        if r.startswith(prefix):
            return label
    return None


def plain_reason(reason):
    """Shorter wording for the user's report."""
    r = re.sub(r"^Confirm:\s*", "", reason or "")
    r = re.sub(r"Known OCR error \((.+?) -> (.+?), Subtitle Edit list\)\.?", r"OCR misread: \1 should be \2.", r)
    r = re.sub(r"; lowercase everywhere else in the file", "", r)
    r = r.replace("Fansub/ripper credit; delete.", "Fansub or download-site credit.")
    return r


def _bucket(row):
    if not row["fixes"]:
        return "call"  # nothing automatic to apply: never an empty formatting or correction row
    if row["cat"] == "Structure" or _label(row["reason"]):
        return "format"
    if row.get("sev") == "check" or (row.get("suggested") or "").startswith("Delete cue if"):
        return "call"
    return "fix"


def show_ws(line):
    """Make whitespace changes visible in the report: stray spaces as ␣, non-breaking spaces as ⍽."""
    line = line.replace("\u00a0", "⍽").replace("\u202f", "⍽").replace("\u2009", "⍽")
    lead = len(line) - len(line.lstrip(" "))
    trail = len(line) - len(line.rstrip(" "))
    core = line[lead:len(line) - trail] if trail else line[lead:]
    core = re.sub(r" {2,}", lambda m: "␣" * len(m.group(0)), core)
    return "␣" * lead + core + "␣" * trail


def _per_cue(path, row, cues_by_n, line_to_cue):
    """Split a row's fixes by cue and compute each cue's current and corrected text."""
    by_cue = {}
    for fx in row["fixes"]:
        c = fx.get("cue") or line_to_cue.get(fx.get("line_no")) or fx.get("delete_cue")
        if c is None and row["cues"]:
            c = row["cues"][0]
        by_cue.setdefault(c, []).append(fx)
    if not by_cue:  # flag-only row
        by_cue = {c: [] for c in row["cues"]} or {None: []}
    out = []
    for c, fixes in sorted(by_cue.items(), key=lambda kv: kv[0] or 0):
        cue = cues_by_n.get(c)
        if not cue:
            out.append((c, None, row["current"], row["suggested"], fixes))
            continue
        lines = [(ln, t) for ln, t in cue["text_lines"] if t.strip()]
        new = {ln: t for ln, t in lines}
        edits = {}
        for fx in fixes:
            if "edit" in fx:
                edits.setdefault(fx["line_no"], []).append(fx["edit"])
        for ln, es in edits.items():
            t = new.get(ln, "")
            for e in sorted(es, key=lambda e: -e["start"]):
                t = t[:e["start"]] + e["new"] + t[e["end"]:]
            new[ln] = t
        for fx in fixes:
            ln = fx.get("line_no")
            if ln is None and "cue" in fx and "line" in fx and 1 <= fx["line"] <= len(lines):
                ln = lines[fx["line"] - 1][0]
            if "replace_line" in fx and ln in new:
                new[ln] = fx["replace_line"]
            elif "find" in fx and ln in new:
                new[ln] = new[ln].replace(fx["find"], fx["replace"], 1)
            elif "delete_line" in fx:
                new.pop(fx["delete_line"], None)
        if any("delete_cue" in fx for fx in fixes):
            sug = "Delete this cue"
        elif fixes:
            sug = " / ".join(show_ws(x) for ln, _ in lines if ln in new for x in new[ln].split("\n"))
        else:
            sug = row["suggested"]
        out.append((c, srt_check.ms_to_ts(cue["start"]), " / ".join(show_ws(t) for _, t in lines), sug, fixes))
    return out


REPEATABLE = {"OCR", "Punctuation", "Spacing"}  # simple mechanical slips that can share one row
SAME_REASON = {"Italics", "Dialogue", "Consistency"}  # identical mechanical fixes (add the missing dash, drop one-word OCR italics)


CHAR_NAMES = {'"': "quote mark", "'": "apostrophe", "-": "hyphen", "- ": "dialogue dash", " ": "space",
              ".": "full stop", ",": "comma", "!": "'!'", "¡": "'¡'", "?": "'?'", "¿": "'¿'", "...": "ellipsis"}


def _slip(cur, sug):
    """The changed characters between two versions of a line, if the change is a single small slip."""
    if not sug or cur == sug:
        return None
    i = 0
    while i < min(len(cur), len(sug)) and cur[i] == sug[i]:
        i += 1
    j = 0
    while j < min(len(cur), len(sug)) - i and cur[-1 - j] == sug[-1 - j]:
        j += 1
    a, b = cur[i:len(cur) - j], sug[i:len(sug) - j]
    return (a, b) if len(a) <= 3 and len(b) <= 3 else None


def combine_repeats(rows, min_lines=3):
    """Render Correction rows, merging the same simple slip on 3+ lines (l -> I, stray "'?") into one row."""
    groups, order = {}, []
    for row in rows:
        c, ts, cur, sug, reason, fx, cat = row
        key = None
        if cat in REPEATABLE:
            sl = _slip(cur, sug)
            if sl:
                key = (cat, sl)
        elif cat in SAME_REASON and sug:
            # same rule on several lines (OK spelling, alright/all right, a lost first dash...): one row
            key = ("reason", re.sub(r"\s*\(.*?\)", "", re.sub(r"\('[^']*'\)", "", reason)).rstrip("."))
        k = key or ("single", c)
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(row)
    combined, singles = [], []
    for k in order:
        rs = groups[k]
        if k[0] != "single" and (len(rs) >= min_lines or (k[0] == "reason" and len(rs) >= 2)):
            combined.append(rs)
        else:
            singles.extend(rs)
    out = []
    for rs in combined:
        k = None
        for kk in order:
            if groups[kk] is rs:
                k = kk
        if True:
            c0, ts0, cur0, sug0 = rs[0][:4]
            lines = ", ".join(str(r[0]) for r in rs)
            if k[0] == "reason":
                why = f"{k[1].rstrip('.')}."
            else:
                reasons = {re.sub(r"\s*\(.*?\)", "", r[4]).rstrip(".") for r in rs}  # main reason, notes dropped
                if len(reasons) == 1:  # the grouped lines share one plain reason: use it
                    why = f"{next(iter(reasons)).rstrip('.')}."
                else:
                    a, b = k[1]
                    name = lambda x: CHAR_NAMES.get(x, f"'{x}'")
                    import unicodedata as _u
                    accent = a and b and len(a) == len(b) == 1 and _u.normalize("NFD", b)[0] == a
                    what = (f"{name(a)} should be {name(b)}" if a and b else f"stray {name(a)} removed" if a
                            else f"missing {name(b)} added")
                    label = "Accent lost" if accent else "OCR misread" if k[0] == "OCR" else "Fix"
                    why = f"{label}: {what}."
            ca, cb = mark_diff(cur0, sug0)
            out.append(f"| {lines} | e.g. {short_ts(ts0)} | e.g. {ca} | e.g. {cb} | {esc(why)} |\n")
    for c, ts, cur, sug, reason, fx, cat in sorted(singles, key=lambda r: r[0] or 0):
        ca, cb = mark_diff(cur, sug)
        out.append(f"| {c} | {short_ts(ts)} | {ca} | {cb} | {esc(reason)} |\n")
    return out


ENCODING_NAMES = {"utf-8-sig": "UTF-8 with BOM", "utf-8": "UTF-8 without BOM", "cp1252": "Windows-1252",
                  "utf-16": "UTF-16"}


def format_wording(f, issues):
    """Error / Suggested fix wording for the file-format row, in the same terms on both sides."""
    enc = ENCODING_NAMES.get(f.get("encoding"), f.get("encoding", "?"))
    ends = f.get("line_endings")
    if not ends:
        crlf = b"\r\n" in open(f["path"], "rb").read()
        ends = "CRLF" if crlf else "LF"
    structure = any(i.get("fix") == "normalize" and i["category"] == "structure" for i in issues)
    no_eol = any(i.get("fix") == "normalize" and i["detail"].startswith("No newline at end") for i in issues)
    err = f"{enc}, {ends}"
    fixd = "UTF-8 without BOM, CRLF"
    if structure or no_eol:
        err += ", cue structure issues"
        fixd += ", clean cue structure"
    return err, fixd


def render(state, out_md):
    """Write three things:
       out_md                 the user's report: per file, Corrections / Needs your call / Formatting
       <out>.working.md       the full row view with IDs, for the read-through's decisions (internal)
       <out>.items.json       what each approvable handle does (file:cue, file:formatting)"""
    order = sorted(range(len(state["files"])), key=lambda i: os.path.basename(state["files"][i]["path"]).lower())
    if order != list(range(len(order))):
        state["files"] = [state["files"][i] for i in order]
        state["rows"] = {str(new + 1): state["rows"].get(str(old + 1), []) for new, old in enumerate(order)}
        if "issues" in state:
            state["issues"] = {str(new + 1): state["issues"].get(str(old + 1), []) for new, old in enumerate(order)}
    files = state["files"]
    multi = len(files) > 1
    work = [f"# Working view (internal): {len(files)} file(s)\n"]
    md = [f"# SRT check: {len(files)} file{'s' if len(files) != 1 else ''}\n"]
    items = {"_files": files}
    totals = {"fix": 0, "call": 0}
    overview = []
    for n, f in enumerate(files, 1):
        rows = state["rows"].get(str(n), [])
        rows.sort(key=lambda r: (ORDER.index(r["cat"]) if r["cat"] in ORDER else len(ORDER),
                                 min([c for c in r["cues"] if c is not None] or [0])))
        name = os.path.basename(f["path"])
        md.append(f"\n## {n}. {name}\n")
        work.append(f"\n## {n}. {name}\n")
        if f.get("error"):
            md.append(f"\nCould not be read: {f['error']}\n")
            continue
        text, _ = srt_check.read_file(f["path"])
        cues, _ = srt_check.parse(text)
        cues_by_n = {c["n"]: c for c in cues}
        line_to_cue = {ln: c["n"] for c in cues for ln, _ in c["text_lines"]}
        fixes_rows, call_rows, fmt = [], [], {}
        if rows:
            work.append("\n| Category | ID | Cue | Timestamp | Current | Suggested | Reason |\n|---|---|---|---|---|---|---|\n")
        name_row = None
        for r in [r for r in rows if r["cat"] == "File name"]:
            name_row = f"| – | – | {esc(r['current'])} | {esc(r['suggested'])} | {esc(plain_reason(r['reason']))} |\n"
            items[f"{n}:file-name"] = {"file": f["path"], "bucket": "fix", "fixes": r["fixes"]}
        for k, r in enumerate(rows, 1):
            rid = f"{n}.{k}" if multi else str(k)
            work.append(f"| {r['cat']} | {rid} | {cue_list(r['cues'])} | {short_ts(r['ts'])} | {esc(r['current'])} | "
                        f"{esc(r['suggested']) if r['suggested'] else '–'} | {esc(r['reason'])} |\n")
            if r["cat"] == "File name":
                continue
            b = _bucket(r)
            if b == "format":
                label = "file format (UTF-8 without BOM, CRLF, cue structure)" if r["cat"] == "Structure" else _label(r["reason"])
                entry = fmt.setdefault(label, {"cues": set(), "fixes": [], "per_cue": {}, "current": r["current"]})
                entry["cues"].update(c for c in r["cues"] if c)
                entry["fixes"] += r["fixes"]
                if r["cat"] != "Structure":
                    for c, ts, cur, sug, fx in _per_cue(f["path"], r, cues_by_n, line_to_cue):
                        pc = entry["per_cue"].setdefault(c, [ts, cur, sug, []])
                        pc[3] += fx
                continue
            reason = plain_reason(r["reason"])
            for c, ts, cur, sug, fx in _per_cue(f["path"], r, cues_by_n, line_to_cue):
                (fixes_rows if b == "fix" else call_rows).append((c, ts, cur, sug, reason, fx, r["cat"]))
        # merge rows that hit the same cue (several fixes on one line show once)
        def merged(rs):
            out = {}
            for c, ts, cur, sug, reason, fx, cat in rs:
                if c in out and c is not None:
                    o = out[c]
                    o[4] = o[4] if reason in o[4] else o[4].rstrip(".") + "; " + reason
                    o[5] = o[5] + fx
                    o[3] = None  # recomputed below
                else:
                    out[c if c is not None else f"x{len(out)}"] = [c, ts, cur, sug, reason, fx, cat]
            res = []
            for o in out.values():
                if o[3] is None:
                    tmp = {"cat": "", "cues": [o[0]], "fixes": o[5], "current": o[2], "suggested": None, "reason": ""}
                    o[3] = _per_cue(f["path"], tmp, cues_by_n, line_to_cue)[0][3]
                res.append(o)
            return sorted(res, key=lambda o: o[0] or 0)
        fixes_rows, call_rows = merged(fixes_rows), merged(call_rows)
        call_cues = {o[0] for o in call_rows}
        fixes_rows = [o for o in fixes_rows if o[0] not in call_cues]  # an open question takes the whole cue
        totals["fix"] += len(fixes_rows)
        totals["call"] += len(call_rows)
        fmt_line = ", ".join(f"{label} ({len(v['cues'])})" for label, v in fmt.items()
                             if not label.startswith("file format"))  # the file-format fix has its own first row
        # corrections = lines changed (spelling, OCR, formatting alike) plus the file-format fix
        changed = {o[0] for o in fixes_rows} | {c for v in fmt.values() for c in v["per_cue"]}
        n_corr = len(changed) + (1 if any(not v["per_cue"] for v in fmt.values()) else 0) + (1 if name_row else 0)
        totals["fix"] += n_corr - len(fixes_rows)
        _k = lambda r: re.sub(r"\s*\(.*?\)", "", r[4])
        n_calls = len({(_k(r) if sum(1 for x in call_rows if _k(x) == _k(r)) > 1 else r[0]) for r in call_rows})
        overview.append((n, name, n_corr, n_calls))
        _k = lambda r: re.sub(r"\s*\(.*?\)", "", r[4])
        n_calls = len({(_k(r) if sum(1 for x in call_rows if _k(x) == _k(r)) > 1 else r[0]) for r in call_rows})
        md.append(f"\n{n_corr} correction{'s' if n_corr != 1 else ''} · "
                  f"{n_calls} need{'s' if n_calls == 1 else ''} your call\n")
        if call_rows:
            md.append("\n### Needs your call\n\n| Line # | Timestamp | Text | Possible fix | Why it needs a call |\n"
                      "|---|---|---|---|---|\n")
            # the same question on several lines (e.g. single-hyphen endings) is asked once, listing every line
            grouped = {}
            for row in call_rows:
                same = sum(1 for x in call_rows if re.sub(r"\s*\(.*?\)", "", x[4]) == re.sub(r"\s*\(.*?\)", "", row[4]))
                key = re.sub(r"\s*\(.*?\)", "", row[4]) if same > 1 else ("one", row[0])
                grouped.setdefault(key, []).append(row)
            for key, rs in grouped.items():
                for c, ts, cur, sug, reason, fx, cat in rs:
                    items[f"{n}:{c}"] = {"file": f["path"], "cue": c, "bucket": "call", "fixes": fx}
                c, ts, cur, sug, reason, fx, cat = rs[0]
                ca, cb = mark_diff(cur, sug)
                if len(rs) > 1:
                    lines = ", ".join(str(r[0]) for r in rs)
                    md.append(f"| {lines} | e.g. {short_ts(ts)} | e.g. {ca} | e.g. {cb} | "
                              f"{esc(re.sub(chr(92) + 's*' + chr(92) + '(.*?' + chr(92) + ')', '', reason))} |\n")
                else:
                    md.append(f"| {c if c is not None else '–'} | {short_ts(ts)} | {ca} | {cb if sug else '–'} | "
                              f"{esc(reason)} |\n")
        if fixes_rows or fmt or name_row:
            md.append("\n### Corrections\n\n| Line # | Timestamp | Error | Suggested fix | Reason |\n|---|---|---|---|---|\n")
            for c, ts, cur, sug, reason, fx, cat in fixes_rows:
                items[f"{n}:{c}"] = {"file": f["path"], "cue": c, "bucket": "fix", "fixes": fx}
            rendered = combine_repeats(fixes_rows)
            n_combined = sum(1 for r_ in rendered if "| e.g. " in r_)
            fmt_rows = []
            file_fmt_row = None
            for k_, (label, v) in enumerate(fmt.items()):
                if not v["per_cue"]:  # file format: always the first row of Corrections
                    items[f"{n}:file-format"] = {"file": f["path"], "bucket": "format", "fixes": v["fixes"]}
                    err, fixd = format_wording(f, state["issues"].get(str(n), []) if "issues" in state else [])
                    file_fmt_row = f"| – | – | {esc(err)} | {esc(fixd)} | File format. |\n"
                    continue
                pcs = sorted(v["per_cue"].items(), key=lambda kv: kv[0] or 0)
                for c, (ts, cur, sug, fx) in pcs:
                    items[f"{n}:{c}:fmt{k_}"] = {"file": f["path"], "cue": c, "bucket": "format", "fixes": fx}
                c0, (ts0, cur0, sug0, _) = pcs[0]
                lines = ", ".join(str(c) for c, _ in pcs)
                eg = "e.g. " if len(pcs) > 1 else ""
                ca, cb = mark_diff(cur0, sug0)
                fmt_rows.append(f"| {lines} | {eg}{short_ts(ts0)} | {eg}{ca} | {eg}{cb} | "
                                f"{esc(label[:1].upper() + label[1:])}. |\n")
            md.extend(([name_row] if name_row else []) + ([file_fmt_row] if file_fmt_row else [])
                      + rendered[:n_combined] + fmt_rows + rendered[n_combined:])
        if not rows:
            md.append("\nNo issues found.\n")
    head = ["\n| # | File | Corrections | Needs your call |\n|---|---|---|---|\n"]
    for n, name, nf, nc in overview:
        head.append(f"| {n} | {esc(name)} | {nf} | {nc} |\n")
    md = md[:1] + head + md[1:]
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write("".join(md))
    with open(os.path.splitext(out_md)[0] + ".working.md", "w", encoding="utf-8") as fh:
        fh.write("".join(work))
    with open(os.path.splitext(out_md)[0] + ".items.json", "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)
    state["totals"] = totals
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ledger", help="srt_qa_ledger.json from earlier runs; files already QC'd and unchanged are skipped")
    ap.add_argument("--lexicon", help="srt_qa_lexicon.txt: accepted words (names, slang) that aren't flagged as unknown")
    a = ap.parse_args()
    ledger = json.load(open(a.ledger, encoding="utf-8")) if a.ledger and os.path.exists(a.ledger) else {}
    lexicon = os.path.abspath(a.lexicon) if a.lexicon else os.path.join(os.path.dirname(os.path.abspath(a.out)),
                                                                          "srt_qa_lexicon.txt")
    srt_check.load_accepted(lexicon)
    cache = {}
    known = {v["sha256"] for v in ledger.values()}
    files, rows, skipped = [], {}, []
    for path in a.files:
        digest = sha256(path)
        if digest in known:
            skipped.append(os.path.basename(path))
            continue
        entry = {"path": os.path.abspath(path), "sha256": digest}
        try:
            data = srt_check.check_file(path)
            data.pop("_cues", None)
            cache[str(len(files) + 1)] = data["issues"]
            entry.update(cue_count=data["cue_count"], encoding=data["encoding"], line_endings=data["line_endings"])
            files.append(entry)
            rows[str(len(files))] = rows_for(path, data)
        except Exception as e:  # report and carry on
            entry["error"] = f"{type(e).__name__}: {e}"
            files.append(entry)
    if not files:
        print(f"Nothing to check: all {len(skipped)} file(s) are already in the ledger.")
        return
    state = {"files": files, "rows": rows, "lexicon": lexicon, "issues": cache}
    items = render(state, a.out)  # sorts rows in place, so state order == report IDs
    with open(os.path.splitext(a.out)[0] + ".state.json", "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    t = state.get("totals", {})
    print(f"{len(files)} file(s): {t.get('fix', 0)} corrections, {t.get('call', 0)} need a call -> {a.out}")
    if skipped:
        print(f"Skipped {len(skipped)} file(s) already in the ledger: {', '.join(skipped[:10])}"
              + (" ..." if len(skipped) > 10 else ""))


if __name__ == "__main__":
    main()
