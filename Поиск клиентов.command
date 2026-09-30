#!/bin/bash
# Двойной клик запускает приложение.
cd "$(dirname "$0")"
if [ -x .venv/bin/python3 ]; then
  exec .venv/bin/python3 run.py
fi
exec python3 run.py
