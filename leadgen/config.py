from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import yaml

DEFAULTS: Dict[str, Any] = {
    "db": "data/leads.db",
    "export_dir": "exports",
    "search": {
        "niche": "",
        "cities": [],
        "limit_per_city": 200,
    },
    "sources": {
        "osm": {"enabled": True, "tags": []},
        "google_places": {"enabled": False, "max_pages": 3},
        "directory": {"enabled": False, "sites": []},
        "social": {"enabled": False, "networks": ["instagram"], "max_results": 40},
    },
    "verify": {
        "region": "UA",
        "check_website": True,
        "search_for_missing_site": False,
        "timeout": 8,
        "workers": 8,
    },
    "gemini": {
        "model": "gemini-2.5-flash",
        "api_key_env": "GEMINI_API_KEY",
        "temperature": 0.7,
        "batch_size": 12,
        "rpm": 12,
        "max_retries": 4,
    },
    "filter": {
        "min_score": 55,
        "rules": "",
    },
    "messages": "messages.yaml",
}


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_dotenv(path: str = ".env") -> None:
    """Минимальный .env-лоадер: без внешней зависимости, уже выставленное окружение не трогает."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


class Config:
    def __init__(self, data: Dict[str, Any], path: Optional[str] = None) -> None:
        self.data = data
        self.path = path

    @classmethod
    def load(cls, path: str = "config.yaml") -> "Config":
        load_dotenv()
        if not os.path.exists(path):
            raise FileNotFoundError(
                "Нет файла %s. Скопируй config.example.yaml в config.yaml и правь его." % path
            )
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls(_deep_merge(DEFAULTS, raw), path)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # частые обращения, чтобы не тащить строки по всему коду
    @property
    def db(self) -> str:
        return self.get("db", "data/leads.db")

    @property
    def cities(self) -> List[str]:
        cities = self.get("search.cities") or []
        return [c for c in cities if c]

    @property
    def niche(self) -> str:
        return self.get("search.niche", "") or ""

    @property
    def messages_path(self) -> str:
        return self.get("messages", "messages.yaml")

    def gemini_key(self) -> str:
        env_name = self.get("gemini.api_key_env", "GEMINI_API_KEY")
        key = os.environ.get(env_name, "").strip()
        if not key:
            raise RuntimeError(
                "Не найден ключ Gemini в переменной %s. "
                "Положи его в .env или экспортируй в шелле." % env_name
            )
        return key


def load_messages(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(
            "Нет файла шаблонов %s. Скопируй messages.example.yaml и напиши свои тексты." % path
        )
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}
