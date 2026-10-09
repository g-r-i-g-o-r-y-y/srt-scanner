"""
What the app does, independent of how it's served: the local server (server.py) and the in-browser
build (webapi.py, under Pyodide) both call these functions.

Each check is a "run": a folder under the data directory holding the uploaded files, the report
the engine writes, and the corrected files once fixes are applied.

  <data>/accepted_words.txt     words you've accepted (names, slang); never flagged again
  <data>/srt_qa_ledger.json     files already finished; skipped next time unless they change
  <data>/runs/<stamp>/input/    the files you checked (copies; your originals are never touched)
  <data>/runs/<stamp>/report.md the full report, same format as the srt-qa skill
  <data>/runs/<stamp>/corrected/  corrected files and one zip of them
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "engine", "scripts")
VENDOR = os.path.join(ROOT, "vendor")
sys.path[:0] = [VENDOR, SCRIPTS]
import digest  # noqa: E402
import srt_check  # noqa: E402

DATA = None  # data directory; set by server.serve() or webapi
LOCK = threading.Lock()


def engine(script, *args):
    """Run one engine script in its own process (the checker keeps module-level state)."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([VENDOR, SCRIPTS]), PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                           else f"{script} failed")
    return r.stdout


def lexicon_path():
    return os.path.join(DATA, "accepted_words.txt")


def ledger_path():
    return os.path.join(DATA, "srt_qa_ledger.json")


def run_dir(run):
    if not re.fullmatch(r"[\w-]+", run or ""):
        raise ValueError("bad run id")
    d = os.path.join(DATA, "runs", run)
    if not os.path.isdir(d):
        raise ValueError("no such run")
    return d


def safe_name(name):
    name = os.path.basename(name.replace("\\", "/"))
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "file"


def new_run():
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    d = os.path.join(DATA, "runs", stamp)
    k = 1
    while os.path.exists(d):
        k += 1
        d = os.path.join(DATA, "runs", f"{stamp}-{k}")
    os.makedirs(os.path.join(d, "input"))
    return os.path.basename(d)


def save_upload(run, name, body):
    """Store one uploaded .srt, or every .srt inside an uploaded .zip."""
    inp = os.path.join(run_dir(run), "input")
    saved = []
    if name.lower().endswith(".zip"):
        import io
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            for m in z.infolist():
                if not m.is_dir() and m.filename.lower().endswith(".srt") and "__MACOSX" not in m.filename:
                    n = safe_name(m.filename)
                    with open(os.path.join(inp, n), "wb") as fh:
                        fh.write(z.read(m))
                    saved.append(n)
    elif name.lower().endswith(".srt"):
        n = safe_name(name)
        with open(os.path.join(inp, n), "wb") as fh:
            fh.write(body)
        saved.append(n)
    return saved


def similar_names(path):
    """Capitalised names used mid-sentence that are one or two letters apart (Kenji / Kenzi)."""
    text, _ = srt_check.read_file(path)
    cues, _ = srt_check.parse(text)
    lex = srt_check.Lexicon(cues)
    line = digest.names_line(cues, lex)
    m = re.search(r"^SIMILAR: (.+)$", line, re.M)
    if not m:
        return []
    out = []
    for pair in m.group(1).split():
        names = pair.split("~")
        where = {}
        for nm in names:
            pat = re.compile(r"\b" + re.escape(nm) + r"\b")
            where[nm] = [c["n"] for c in cues if any(pat.search(srt_check.visible(t)) for _, t in c["text_lines"])]
        out.append({"names": names, "cues": where})
    return out


def check(run, recheck):
    d = run_dir(run)
    inp = os.path.join(d, "input")
    files = sorted((os.path.join(inp, x) for x in os.listdir(inp) if x.lower().endswith(".srt")),
                   key=lambda p: os.path.basename(p).lower())
    if not files:
        raise ValueError("No .srt files were uploaded.")
    report = os.path.join(d, "report.md")
    args = [*files, "--out", report, "--lexicon", lexicon_path()]
    if not recheck:
        args += ["--ledger", ledger_path()]
    summary = engine("build_report.py", *args).strip()
    items_path = os.path.join(d, "report.items.json")
    if not os.path.exists(items_path):  # every file was already finished
        return {"run": run, "summary": summary, "files": []}
    items = json.load(open(items_path, encoding="utf-8"))
    state = json.load(open(os.path.join(d, "report.state.json"), encoding="utf-8"))
    out = []
    for n, f in enumerate(items["_files"], 1):
        unknown = {}
        for i in state.get("issues", {}).get(str(n), []):
            if i.get("detail", "").startswith("Unknown word") and i.get("token"):
                unknown.setdefault(i["cue"], [])
                if i["token"] not in unknown[i["cue"]]:
                    unknown[i["cue"]].append(i["token"])
        rows = []
        for k, it in items.items():
            if k.startswith(f"{n}:"):
                v = dict(it.get("view", {}))
                rows.append(dict(key=k, bucket=it["bucket"], cue=it.get("cue"), has_fix=bool(it["fixes"]),
                                 unknown=unknown.get(it.get("cue"), []) if it["bucket"] == "call" else [], **v))
        out.append({"n": n, "name": os.path.basename(f["path"]), "error": f.get("error"),
                    "cue_count": f.get("cue_count"), "rows": rows,
                    "names": [] if f.get("error") else similar_names(f["path"])})
    skipped = re.search(r"Skipped (\d+) file", summary)
    return {"run": run, "summary": summary, "skipped": int(skipped.group(1)) if skipped else 0, "files": out}


def edited_fixes(it, lines):
    """The user's own text for a cue, as fixes: each line replaced, extra lines added, missing ones removed."""
    nos = it["view"]["line_nos"]
    lines = [ln.rstrip() for ln in lines]
    while lines and not lines[-1].strip():
        lines.pop()
    if not any(ln.strip() for ln in lines):
        return [{"delete_cue": it["cue"]}]
    fixes = []
    for i, no in enumerate(nos):
        if i < len(lines):
            text = lines[i] if i < len(nos) - 1 else "\n".join(lines[i:])
            fixes.append({"line_no": no, "replace_line": text})
        else:
            fixes.append({"delete_line": no})
    return fixes


def apply(run, picked, edits):
    d = run_dir(run)
    items = json.load(open(os.path.join(d, "report.items.json"), encoding="utf-8"))
    chosen = {"_files": items["_files"]}
    edited_cues = {}
    for k, lines in edits.items():
        if k in items and k in picked and items[k].get("cue") is not None:
            edited_cues[(k.split(":")[0], items[k]["cue"])] = k
    for k in picked:
        it = items.get(k)
        if not it or k == "_files":
            continue
        owner = edited_cues.get((k.split(":")[0], it.get("cue")))
        if owner and owner != k:
            continue  # the user's own text for this cue already includes (or overrides) this fix
        it = dict(it, bucket="fix")
        if owner == k:
            it["fixes"] = edited_fixes(it, edits[k])
        chosen[k] = it
    sel = os.path.join(d, "approved.items.json")
    json.dump(chosen, open(sel, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    outdir = os.path.join(d, "corrected")
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir)
    if os.path.exists(ledger_path()):
        shutil.copy(ledger_path(), os.path.join(outdir, "srt_qa_ledger.json"))
    log = engine("apply_approved.py", sel, "all", outdir)
    led = os.path.join(outdir, "srt_qa_ledger.json")
    if os.path.exists(led):
        shutil.move(led, ledger_path())
    zips = [x for x in os.listdir(outdir) if x.endswith(".zip")]
    srts = sorted(x for x in os.listdir(outdir) if x.lower().endswith(".srt"))
    skipped = [ln.strip() for ln in log.splitlines() if ln.strip().startswith("SKIP")]
    bad = [ln for ln in log.splitlines() if "Timestamps unchanged: False" in ln]
    return {"log": log, "files": srts, "zip": zips[0] if zips else None, "folder": outdir,
            "applied": sum(1 for k in picked if items.get(k, {}).get("fixes") or k in edits),
            "skipped": skipped, "timestamp_problem": bool(bad)}


def accept_word(word):
    word = word.strip()
    if not re.fullmatch(r"[\w'’-]{1,60}", word):
        raise ValueError("not a word")
    with LOCK:
        existing = set()
        if os.path.exists(lexicon_path()):
            existing = {ln.split("#", 1)[0].strip().lower() for ln in open(lexicon_path(), encoding="utf-8")}
        if word.lower() not in existing:
            new = not os.path.exists(lexicon_path())
            with open(lexicon_path(), "a", encoding="utf-8") as fh:
                if new:
                    fh.write("# Words accepted in SRT Scanner: names, slang and terms that aren't OCR errors.\n")
                fh.write(word.lower() + "\n")
    return {"ok": True}
