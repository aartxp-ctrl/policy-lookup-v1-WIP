#!/bin/bash
# ONE-TIME setup with internet (fresh copy from GitHub). Afterwards Run.command works offline.
cd "$(dirname "$0")"
PY="runtime/mac/python/bin/python3.12"
if [ ! -x "$PY" ]; then
  echo "Downloading a private copy of Python into this folder..."
  mkdir -p runtime/mac
  curl -L --fail -o runtime/python-mac.tar.gz "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.12.16%2B20261110-aarch64-apple-darwin-install_only.tar.gz" || exit 1
  tar -xzf runtime/python-mac.tar.gz -C runtime/mac && rm runtime/python-mac.tar.gz
fi
"$PY" setup_runtime.py && echo "Setup finished. Double-click Run.command to start (no internet needed)."
read -n1 -p "Press any key to close"
