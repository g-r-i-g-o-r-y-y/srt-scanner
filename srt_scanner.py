#!/usr/bin/env python3
"""
SRT Scanner: offline QA for OCR'd English subtitles.

  python srt_scanner.py                       open the app in your browser (default)
  python srt_scanner.py check a.srt b.srt     write a Markdown report without the app
          [--out report.md] [--recheck]
  python srt_scanner.py apply report.items.json "all, !1:240" corrected/
                                              apply fixes from a report (same approval
                                              syntax as the srt-qa skill)

Options for the app:
  --data DIR     where runs, accepted words and the ledger are kept (default ~/SRT Scanner)
  --port N       fixed port instead of a free one
  --no-browser   don't open a browser window

Needs only Python 3.8+. Nothing is sent anywhere: the app listens on 127.0.0.1 only.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(ROOT, "engine", "scripts")
VENDOR = os.path.join(ROOT, "vendor")
DEFAULT_DATA = os.path.join("~", "SRT Scanner")


def run_script(script, args):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([VENDOR, SCRIPTS]), PYTHONIOENCODING="utf-8")
    return subprocess.call([sys.executable, os.path.join(SCRIPTS, script), *args], env=env)


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("check", "apply"):
        cmd, rest = sys.argv[1], sys.argv[2:]
        data = os.path.abspath(os.path.expanduser(DEFAULT_DATA))
        os.makedirs(data, exist_ok=True)
        if cmd == "check":
            ap = argparse.ArgumentParser(prog="srt_scanner.py check")
            ap.add_argument("files", nargs="+")
            ap.add_argument("--out", default="srt_report.md")
            ap.add_argument("--recheck", action="store_true", help="check files the ledger says are finished")
            a = ap.parse_args(rest)
            args = [*a.files, "--out", a.out, "--lexicon", os.path.join(data, "accepted_words.txt")]
            if not a.recheck:
                args += ["--ledger", os.path.join(data, "srt_qa_ledger.json")]
            sys.exit(run_script("build_report.py", args))
        sys.exit(run_script("apply_approved.py", rest))

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, ROOT)
    from app import server
    server.serve(a.data, a.port, not a.no_browser)


if __name__ == "__main__":
    main()
