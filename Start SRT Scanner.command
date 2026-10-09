#!/bin/sh
# Double-click on macOS (or run from a terminal on Linux) to start SRT Scanner.
cd "$(dirname "$0")" || exit 1
if [ ! -f srt_scanner.py ]; then
  echo "This launcher has to stay in the SRT Scanner folder, next to srt_scanner.py and the"
  echo "app, engine and vendor folders. Download the whole repository as a ZIP, unzip it, and"
  echo "run this file from the unzipped folder."
  exit 1
fi
exec python3 srt_scanner.py "$@"
