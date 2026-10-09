"""
Entry points for the in-browser build (web/), running under Pyodide.

The browser has no processes, so engine scripts that core.py and apply_approved.py would start with
subprocess.run are run in this interpreter instead (run_in_process). Data lives in DATA, which the
page mounts as IndexedDB storage so accepted words and the ledger survive between visits; uploaded
files and corrected output (DATA/runs) are cleared on each page load.
"""
import contextlib
import io
import json
import os
import runpy
import shutil
import subprocess
import sys

from app import core

DATA = "/data"


def run_in_process(cmd, capture_output=False, text=False, encoding=None, env=None, **kw):
    """subprocess.run stand-in for `python script.py args...`: runs the script here and captures output."""
    argv = list(cmd[1:])
    out, err = io.StringIO(), io.StringIO()
    old_argv, code = sys.argv, 0
    sys.argv = argv
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                runpy.run_path(argv[0], run_name="__main__")
            except SystemExit as e:
                if isinstance(e.code, int):
                    code = e.code
                elif e.code is not None:
                    err.write(f"{e.code}\n")
                    code = 1
            except Exception as e:  # report like a crashed process would
                err.write(f"{type(e).__name__}: {e}\n")
                code = 1
    finally:
        sys.argv = old_argv
    o, e_ = out.getvalue(), err.getvalue()
    if not (text or encoding):
        o, e_ = o.encode("utf-8"), e_.encode("utf-8")
    return subprocess.CompletedProcess(cmd, code, o, e_)


subprocess.run = run_in_process


def start():
    core.DATA = DATA
    shutil.rmtree(os.path.join(DATA, "runs"), ignore_errors=True)
    os.makedirs(os.path.join(DATA, "runs"), exist_ok=True)
    # load the dictionary now, while the page says it's loading, rather than on the first check
    core.srt_check.Lexicon([])


def new_run():
    return core.new_run()


def upload(run, name, data):
    data = data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)
    return json.dumps(core.save_upload(run, name, data))


def check(run, recheck):
    return json.dumps(core.check(run, bool(recheck)), ensure_ascii=False)


def apply(run, picked, edits):
    r = core.apply(run, json.loads(picked), json.loads(edits))
    r["folder"] = None  # not a folder the user can open
    return json.dumps(r, ensure_ascii=False)


def accept_word(word):
    core.accept_word(word)


def file_path(run, name):
    """Path of a downloadable file in a run (the report or a corrected file), or None."""
    d = core.run_dir(run)
    allowed = {"report.md": os.path.join(d, "report.md")}
    cor = os.path.join(d, "corrected")
    if os.path.isdir(cor):
        allowed.update({x: os.path.join(cor, x) for x in os.listdir(cor)})
    p = allowed.get(name)
    return p if p and os.path.exists(p) else None


def export_settings():
    words = open(core.lexicon_path(), encoding="utf-8").read() if os.path.exists(core.lexicon_path()) else ""
    ledger = json.load(open(core.ledger_path(), encoding="utf-8")) if os.path.exists(core.ledger_path()) else {}
    return json.dumps({"srt_scanner": 1, "accepted_words": words, "ledger": ledger}, ensure_ascii=False, indent=1)


def import_settings(name, text):
    """Merge a settings export, an accepted_words.txt or a srt_qa_ledger.json into what's stored."""
    words, ledger = [], {}
    if name.lower().endswith(".txt"):
        words = text.splitlines()
    else:
        data = json.loads(text)
        if "srt_scanner" in data:
            words, ledger = data.get("accepted_words", "").splitlines(), data.get("ledger", {})
        else:  # a bare ledger, as the desktop app and the skill write it
            ledger = data
    added = 0
    for w in words:
        w = w.split("#", 1)[0].strip()
        if w:
            try:
                before = os.path.getsize(core.lexicon_path()) if os.path.exists(core.lexicon_path()) else 0
                core.accept_word(w)
                added += os.path.getsize(core.lexicon_path()) != before
            except ValueError:
                pass
    if ledger:
        cur = json.load(open(core.ledger_path(), encoding="utf-8")) if os.path.exists(core.ledger_path()) else {}
        cur.update({k: v for k, v in ledger.items() if isinstance(v, dict) and "sha256" in v})
        json.dump(cur, open(core.ledger_path(), "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    return json.dumps({"words": added, "files": len(ledger)})


def settings_summary():
    n_words = 0
    if os.path.exists(core.lexicon_path()):
        n_words = sum(1 for ln in open(core.lexicon_path(), encoding="utf-8") if ln.split("#", 1)[0].strip())
    n_files = len(json.load(open(core.ledger_path(), encoding="utf-8"))) if os.path.exists(core.ledger_path()) else 0
    return json.dumps({"words": n_words, "files": n_files})


def forget_settings():
    for p in (core.lexicon_path(), core.ledger_path()):
        if os.path.exists(p):
            os.remove(p)
