#!/bin/sh
# Double-click on macOS (or run from a terminal on Linux) to start SRT Scanner.
cd "$(dirname "$0")" && exec python3 srt_scanner.py "$@"
