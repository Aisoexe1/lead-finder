"""Чтение и запись config.yaml, messages.yaml и .env из интерфейса.

Про пути. В собранном виде (PyInstaller) программа лежит внутри временной
распакованной папки, писать туда нельзя и бессмысленно: при следующем запуске
её содержимое пропадёт. Поэтому файлы делятся надвое:

* ресурсы (интерфейс, образцы настроек) читаются из сборки;
* всё, что правит пользователь и что копится при работе, лежит рядом
  с исполняемым файлом.

Из исходников оба пути совпадают с папкой проекта.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import yaml

from leadgen.config import Config, load_dotenv

_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = getattr(sys, "frozen", False)

APP_NAME = "ПоискКлиентов"

# откуда читать вшитое
RES_DIR = getattr(sys, "_MEIPASS", _SOURCE_ROOT)


def _writable(folder: str) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write-test")
        with open(probe, "w") as fh:
            fh.write("")
        os.remove(probe)
        return True
    except OSError:
        return False


def _data_root() -> str:
    """Где хранить настройки, ключ и базу.

    Из исходников это папка проекта. В собранном виде писать внутрь программы
    нельзя: на macOS она лежит в /Applications и при обновлении заменяется
    целиком, то есть данные бы пропали. Поэтому для .app сразу уходим
    в Application Support, а на других системах пробуем папку рядом
    с программой и откатываемся в пользовательскую, если она не пишется.
    """
    if not FROZEN:
        return _SOURCE_ROOT

    beside = os.path.dirname(sys.executable)

    if sys.platform == "darwin" and ".app/Contents/" in sys.executable:
        return os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", APP_NAME)

    if _writable(beside):
        return beside

    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    return os.path.join(os.path.expanduser("~"), ".config", APP_NAME)


# куда писать пользовательское
ROOT = _data_root()

CONFIG_PATH = os.path.join(ROOT, "config.yaml")
MESSAGES_PATH = os.path.join(ROOT, "messages.yaml")
ENV_PATH = os.path.join(ROOT, ".env")
CONFIG_EXAMPLE = os.path.join(RES_DIR, "config.example.yaml")
MESSAGES_EXAMPLE = os.path.join(RES_DIR, "messages.example.yaml")

UI_INDEX = os.path.join(RES_DIR, "ui", "index.html")


def ensure_files() -> None:
    """Первый запуск: разворачиваем образцы и готовим папки для данных."""
    os.makedirs(ROOT, exist_ok=True)
    for example, target in (
        (CONFIG_EXAMPLE, CONFIG_PATH),
        (MESSAGES_EXAMPLE, MESSAGES_PATH),
    ):
        if not os.path.exists(target) and os.path.exists(example):
            with open(example, "r", encoding="utf-8") as src:
                content = src.read()
            with open(target, "w", encoding="utf-8") as dst:
                dst.write(content)

    if not os.path.exists(ENV_PATH):
        with open(ENV_PATH, "w", encoding="utf-8") as fh:
            fh.write("GEMINI_API_KEY=\n")

    for folder in ("data", "exports"):
        os.makedirs(os.path.join(ROOT, folder), exist_ok=True)


def read_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def write_yaml(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False, width=100)


def config() -> Config:
    """Конфиг с путями, приведёнными к абсолютным.

    В файле они записаны относительно проекта, а рабочая папка у запущенного
    приложения может быть любой, поэтому разворачиваем их от ROOT.
    """
    load_dotenv(ENV_PATH)
    cfg = Config.load(CONFIG_PATH)

    db = cfg.data.get("db", "data/leads.db")
    if not os.path.isabs(db):
        cfg.data["db"] = os.path.join(ROOT, db)

    export_dir = cfg.data.get("export_dir", "exports")
    if not os.path.isabs(export_dir):
        cfg.data["export_dir"] = os.path.join(ROOT, export_dir)

    # кэш источников тоже кладём рядом с данными, а не в текущую папку
    osm = (cfg.data.get("sources") or {}).get("osm")
    if isinstance(osm, dict):
        cache_dir = osm.get("cache_dir") or os.path.join("data", "overpass")
        if not os.path.isabs(cache_dir):
            osm["cache_dir"] = os.path.join(ROOT, cache_dir)

    return cfg


def read_config() -> Dict[str, Any]:
    return read_yaml(CONFIG_PATH)


def save_config(data: Dict[str, Any]) -> None:
    write_yaml(CONFIG_PATH, data)


def read_messages() -> Dict[str, Any]:
    return read_yaml(MESSAGES_PATH)


def save_messages(data: Dict[str, Any]) -> None:
    write_yaml(MESSAGES_PATH, data)


def api_key() -> str:
    load_dotenv(ENV_PATH)
    return os.environ.get("GEMINI_API_KEY", "").strip()


def save_api_key(key: str) -> None:
    """Ключ хранится в .env рядом с программой и в config.yaml не попадает."""
    lines: List[str] = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            lines = [line.rstrip("\n") for line in fh]
    for index, line in enumerate(lines):
        if line.startswith("GEMINI_API_KEY="):
            lines[index] = "GEMINI_API_KEY=%s" % key
            break
    else:
        lines.append("GEMINI_API_KEY=%s" % key)
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.environ["GEMINI_API_KEY"] = key
