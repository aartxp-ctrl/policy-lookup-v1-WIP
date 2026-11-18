#!/bin/bash
# Double-click to start Policy Lookup on a Mac (Apple Silicon). Fully offline.
cd "$(dirname "$0")"
PY="runtime/mac/python/bin/python3.12"
if [ ! -x "$PY" ]; then echo "Bundled Python not found at $PY"; read -n1 -p "Press any key to close"; exit 1; fi
"$PY" launch.py
read -n1 -p "Press any key to close this window"
