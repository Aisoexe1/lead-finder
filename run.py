#!/usr/bin/env python3
"""Запуск приложения: python3 run.py

Окно нативное (Cocoa/WKWebView), интерфейс внутри него на HTML и CSS.
Никакого браузера и никакого сервера: страница грузится с диска, а с ядром
общается через мост pywebview, а не по сети.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import webview  # noqa: E402

from app import settings  # noqa: E402
from app.api import Api  # noqa: E402

BACKGROUND = "#0d0d0f"


def main() -> int:
    api = Api()
    index = settings.UI_INDEX
    if not os.path.exists(index):
        print("Не найден интерфейс: %s" % index)
        return 1

    window = webview.create_window(
        "Поиск клиентов",
        index,
        js_api=api,
        width=1320,
        height=900,
        min_size=(1020, 680),
        background_color=BACKGROUND,
        text_select=True,
    )
    api.window = window

    # LEADFINDER_DEBUG=1 открывает веб-инспектор, удобно при правке интерфейса
    webview.start(debug=bool(os.environ.get("LEADFINDER_DEBUG")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
