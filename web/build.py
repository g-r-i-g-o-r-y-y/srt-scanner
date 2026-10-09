#!/usr/bin/env python3
"""
Build the GitHub Pages site: the app running entirely in the browser under Pyodide.

  python web/build.py --pyodide path/to/pyodide --out _site

--pyodide is an unpacked pyodide-core release (the folder holding pyodide.js). The GitHub Actions
workflow downloads it; locally, get pyodide-core-<version>.tar.bz2 from
https://github.com/pyodide/pyodide/releases and unpack it.

The site is static: index.html (app/ui.html plus web/web.js), app.zip (app/, engine/, vendor/,
unpacked into Pyodide on load), Pyodide itself, and a service worker that caches all of it.
"""
import argparse
import hashlib
import json
import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYODIDE_FILES = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]
BUNDLE = ["app/__init__.py", "app/core.py", "app/webapi.py", "engine", "vendor"]


def bundle(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for item in BUNDLE:
            src = os.path.join(ROOT, item)
            paths = [src] if os.path.isfile(src) else sorted(
                os.path.join(d, f) for d, dirs, files in os.walk(src) for f in files if "__pycache__" not in d)
            for p in paths:
                info = zipfile.ZipInfo(os.path.relpath(p, ROOT).replace(os.sep, "/"), (2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, open(p, "rb").read())  # fixed timestamps: same input, same zip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pyodide", required=True)
    ap.add_argument("--out", default="_site")
    a = ap.parse_args()
    out = a.out
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, "pyodide"))

    ui = open(os.path.join(ROOT, "app", "ui.html"), encoding="utf-8").read()
    hook = "<script>\nconst $ = "
    assert ui.count(hook) == 1, "ui.html: main script not found"
    ui = ui.replace(hook, '<script src="pyodide/pyodide.js"></script>\n<script src="web.js"></script>\n' + hook)
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(ui)
    shutil.copy(os.path.join(ROOT, "web", "web.js"), out)
    bundle(os.path.join(out, "app.zip"))
    for f in PYODIDE_FILES:
        shutil.copy(os.path.join(a.pyodide, f), os.path.join(out, "pyodide", f))
    open(os.path.join(out, ".nojekyll"), "w").close()

    files = sorted(os.path.relpath(os.path.join(d, f), out).replace(os.sep, "/")
                   for d, _, fs in os.walk(out) for f in fs if not f.startswith("."))
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode() + open(os.path.join(out, f), "rb").read())
    sw = open(os.path.join(ROOT, "web", "sw.js"), encoding="utf-8").read()
    sw = sw.replace("__VERSION__", h.hexdigest()[:12]).replace("__FILES__", json.dumps(["./"] + files))
    open(os.path.join(out, "sw.js"), "w", encoding="utf-8").write(sw)
    total = sum(os.path.getsize(os.path.join(out, f)) for f in files)
    print(f"Built {out}: {len(files)} files, {total / 1e6:.1f} MB, version {h.hexdigest()[:12]}")


if __name__ == "__main__":
    main()
