#!/usr/bin/env python3
"""
Apply what the user approved from the report.

Usage:
  python apply_approved.py report.items.json "<approval>" <output-dir>

The approval is a comma-separated list, using the file numbers and line numbers shown in the report:
  all              every Correction and every file's Formatting (not "Needs your call")
  !3:240           skip line 240 of file 3
  3:118            apply line 118 of file 3 (use for "Needs your call" items the user accepts, after
                   writing the user's chosen fix into the report with finalize_report.py)
  !3:formatting    skip all of file 3's formatting      3:formatting   apply it
  !3               skip file 3 entirely                 3              apply file 3's corrections and formatting
Each corrected file keeps its original name in <output-dir>; originals are never touched.
A ledger of finished files (srt_qa_ledger.json) is written alongside; it's optional for the user to keep.
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def select(items, spec):
    keys = [k for k in items if not k.startswith("_")]
    fnum = lambda k: k.split(":")[0]
    chosen, excluded = set(), set()
    for tok in [t.strip().lower() for t in spec.split(",") if t.strip()]:
        target = excluded if tok.startswith("!") else chosen
        tok = tok.lstrip("!").strip()
        if tok == "all":
            target.update(k for k in keys if items[k]["bucket"] in ("fix", "format"))
        elif re.fullmatch(r"\d+", tok):
            target.update(k for k in keys if fnum(k) == tok and (target is excluded or items[k]["bucket"] in ("fix", "format")))
        elif re.fullmatch(r"\d+:formatting", tok):
            target.update(k for k in keys if fnum(k) == tok.split(":")[0] and items[k]["bucket"] == "format")
        elif re.fullmatch(r"\d+:[\w-]+", tok):
            hits = [k for k in keys if k == tok or k.startswith(tok + ":")]  # a line's fix and its formatting
            if not hits:
                print(f"  ? nothing at {tok}")
            target.update(hits)
        else:
            print(f"  ? not understood: {tok}")
    return [k for k in keys if k in chosen and k not in excluded]


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    items_path, spec, outdir = sys.argv[1:]
    items = json.load(open(items_path, encoding="utf-8"))
    report_files = items.pop("_files", [])
    picked = select(items, spec)
    os.makedirs(outdir, exist_ok=True)
    per_file, no_fix = {}, []
    for k in picked:
        it = items[k]
        if not it["fixes"]:
            no_fix.append(k)
            continue
        per_file.setdefault(it["file"], []).extend(dict(fx, id=k) for fx in it["fixes"])
    print(f"{len(picked)} item(s) approved across {len(per_file)} file(s).")
    for path, fixes in per_file.items():
        renames = [fx["rename"] for fx in fixes if "rename" in fx]
        fixes = [fx for fx in fixes if "rename" not in fx]
        dst = os.path.join(outdir, renames[-1] if renames else os.path.basename(path))
        if os.path.abspath(dst) == os.path.abspath(path):
            sys.exit("Output directory is the input directory; refusing to overwrite originals.")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(fixes, fh, ensure_ascii=False)
        r = subprocess.run([sys.executable, os.path.join(HERE, "apply_fixes.py"), path, fh.name, dst],
                           capture_output=True, text=True)
        out = r.stdout.splitlines()
        print(f"== {os.path.basename(dst)}: {out[0] if out else r.stderr.strip()}"
              + (f"  (renamed from {os.path.basename(path)})" if renames else ""))
        for line in out[1:]:
            if line.strip().startswith("SKIP"):
                print(line)
        os.unlink(fh.name)
    if no_fix:
        print(f"Approved but no fix written yet: {', '.join(no_fix)}")
    # one zip of every corrected file, so a batch is a single download
    import zipfile
    srts = sorted(x for x in os.listdir(outdir) if x.lower().endswith(".srt"))
    if srts:
        base = os.path.basename(os.path.normpath(outdir))
        if base == "corrected":  # name the zip after the batch folder above it (batch10_corrected_srts.zip)
            base = os.path.basename(os.path.dirname(os.path.normpath(outdir)))
        zpath = os.path.join(outdir, base + "_corrected_srts.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for x in srts:
                z.write(os.path.join(outdir, x), x)
        print(f"Zip: {zpath} ({len(srts)} files)")

    ledger_path = os.path.join(outdir, "srt_qa_ledger.json")
    ledger = json.load(open(ledger_path, encoding="utf-8")) if os.path.exists(ledger_path) else {}
    today = datetime.date.today().isoformat()
    with_items = {it["file"] for it in items.values()}
    for f in report_files:
        name = os.path.basename(f["path"])
        ren = [fx["rename"] for fx in per_file.get(f["path"], []) if "rename" in fx]
        if ren:
            name = ren[-1]
        out = os.path.join(outdir, name)
        if f["path"] in per_file and os.path.exists(out):
            ledger[name] = {"sha256": hashlib.sha256(open(out, "rb").read()).hexdigest(), "date": today}
        elif f["path"] not in with_items:
            ledger[name] = {"sha256": f["sha256"], "date": today}
    with open(ledger_path, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, ensure_ascii=False, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
