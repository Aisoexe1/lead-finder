"""Мост между интерфейсом и ядром.

Каждый метод вызывается из JS как pywebview.api.<имя>(...) и обязан возвращать
что-то JSON-сериализуемое. Исключения тоже превращаются в ответ, иначе на
стороне интерфейса промис отвалится без объяснений.
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
import webbrowser
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

from leadgen.export import export_csv, export_html
from leadgen.models import DROPPED, KEPT, NEW, VERIFIED, WRITTEN, Lead
from leadgen.storage import Store

from . import pipeline, settings
from .tasks import DONE, FAIL, LOG, TaskRunner

STATUS_LABELS = {
    NEW: "не проверен",
    VERIFIED: "ждёт отсева",
    KEPT: "отобран",
    DROPPED: "отсеян",
    WRITTEN: "готов",
}

SOURCES = [
    {"key": "osm", "title": "OpenStreetMap", "note": "Без ключа, основной источник"},
    {"key": "google_places", "title": "Google Places", "note": "Нужен платный ключ Google"},
    {"key": "directory", "title": "Каталоги фирм", "note": "Нужны CSS-селекторы в config.yaml"},
    {"key": "social", "title": "Соцсети", "note": "Через поиск, много шума и мало данных"},
]


def guard(func: Callable) -> Callable:
    """Ошибка внутри метода должна доехать до интерфейса читаемой строкой."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            traceback.print_exc()
            return {"ok": False, "error": str(exc)}

    return wrapper


class Api:
    def __init__(self) -> None:
        settings.ensure_files()
        self.runner = TaskRunner()
        self.lines: List[str] = []
        self.job_done = False
        self.job_error = ""
        self.window = None

    # ------------------------------------------------------------- стартовые

    @guard
    def bootstrap(self) -> Dict[str, Any]:
        config = settings.read_config()
        search = config.get("search") or {}
        sources_cfg = config.get("sources") or {}
        return {
            "ok": True,
            "search": {
                "niche": search.get("niche", ""),
                "cities": search.get("cities") or [],
                "limit": search.get("limit_per_city", 150),
            },
            "sources": [
                dict(item, enabled=bool((sources_cfg.get(item["key"]) or {}).get("enabled")))
                for item in SOURCES
            ],
            "counts": self._counts(),
            "has_key": bool(settings.api_key()),
            "dark": True,
        }

    def _counts(self) -> Dict[str, int]:
        try:
            with Store(settings.config().db) as store:
                return store.counts()
        except Exception:
            return {}

    @guard
    def counts(self) -> Dict[str, Any]:
        return {"ok": True, "counts": self._counts()}

    # ---------------------------------------------------------------- задачи

    @guard
    def run_step(self, step: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.runner.busy:
            return {"ok": False, "error": "Сейчас уже идёт: %s" % self.runner.current}

        params = params or {}
        needs_key = step in ("filter", "full", "check_key") or (
            step == "write"
            and (settings.read_messages().get("mode") or "ai").lower() != "template"
        )
        if needs_key and not settings.api_key():
            return {"ok": False, "error": "no_key"}

        if step in ("scrape", "full"):
            niche = (params.get("niche") or "").strip()
            cities = [c.strip() for c in (params.get("cities") or []) if c.strip()]
            sources = params.get("sources") or []
            limit = int(params.get("limit") or 150)
            if not niche:
                return {"ok": False, "error": "Впиши нишу, например «салон красоты»."}
            if not cities:
                return {"ok": False, "error": "Впиши хотя бы один город."}
            if not sources:
                return {"ok": False, "error": "Отметь хотя бы один источник."}

            self._persist_search(niche, cities, limit, sources)
            target = pipeline.full_run if step == "full" else pipeline.scrape
            title = "Полный прогон" if step == "full" else "Поиск лидов"
            self._start(title, lambda rep: target(rep, niche, cities, limit, sources))
            return {"ok": True}

        jobs = {
            "verify": ("Проверка контактов", pipeline.verify),
            "filter": ("Отсев мусора", pipeline.ai_filter),
            "write": ("Написание сообщений", lambda rep: pipeline.write_messages(rep)),
            "rewrite": ("Перезапись сообщений",
                        lambda rep: pipeline.write_messages(rep, rewrite=True)),
            "check_key": ("Проверка ключа", pipeline.check_key),
        }
        if step not in jobs:
            return {"ok": False, "error": "Неизвестный шаг: %s" % step}
        title, target = jobs[step]
        self._start(title, target)
        return {"ok": True}

    def _persist_search(self, niche: str, cities: List[str], limit: int,
                        sources: List[str]) -> None:
        """Введённое в форме становится новым значением по умолчанию."""
        config = settings.read_config()
        config.setdefault("search", {})
        config["search"]["niche"] = niche
        config["search"]["cities"] = cities
        config["search"]["limit_per_city"] = limit
        config.setdefault("sources", {})
        for item in SOURCES:
            config["sources"].setdefault(item["key"], {})["enabled"] = item["key"] in sources
        settings.save_config(config)

    def _start(self, title: str, target) -> None:
        self.lines = []
        self.job_done = False
        self.job_error = ""
        self.runner.start(title, target)

    @guard
    def job_status(self, since: int = 0) -> Dict[str, Any]:
        """Интерфейс опрашивает этот метод и дочитывает лог с позиции since."""
        for kind, payload in self.runner.drain():
            if kind == LOG:
                self.lines.append(payload)
            elif kind == DONE:
                self.job_done = True
            elif kind == FAIL:
                self.job_done = True
                self.job_error = str(payload)
        return {
            "ok": True,
            "running": self.runner.busy,
            "title": self.runner.current,
            "lines": self.lines[int(since):],
            "total": len(self.lines),
            "done": self.job_done and not self.runner.busy,
            "error": self.job_error,
            "counts": self._counts(),
            "has_key": bool(settings.api_key()),
        }

    # ----------------------------------------------------------------- лиды

    @guard
    def leads(self, status: Optional[str] = None, query: str = "") -> Dict[str, Any]:
        with Store(settings.config().db) as store:
            items = store.fetch(status=status or None)

        needle = (query or "").lower().strip()
        out = []
        for lead in items:
            if needle:
                haystack = " ".join([lead.name, lead.city, lead.category,
                                     lead.phone_e164, lead.ai_reason]).lower()
                if needle not in haystack:
                    continue
            out.append(self._lead_dict(lead))
        return {"ok": True, "leads": out, "counts": self._counts()}

    def _lead_dict(self, lead: Lead) -> Dict[str, Any]:
        phone = lead.phone_e164 or lead.phone
        return {
            "id": lead.id,
            "name": lead.name,
            "city": lead.city,
            "category": lead.category,
            "address": lead.address,
            "phone": phone,
            "phone_e164": lead.phone_e164,
            "email": lead.email,
            "instagram": lead.instagram,
            "facebook": lead.facebook,
            "source": lead.source,
            "score": lead.ai_score,
            "reason": lead.ai_reason,
            "status": lead.status,
            "status_label": "отправлено" if lead.sent_at
                            else STATUS_LABELS.get(lead.status, lead.status),
            "message_1": lead.message_1,
            "message_2": lead.message_2,
            "sent_at": lead.sent_at,
            "maps": ("https://www.google.com/maps/search/?api=1&query=%s,%s"
                     % (lead.lat, lead.lon)) if lead.lat and lead.lon else "",
        }

    @guard
    def lead_save(self, lead_id: str, message_1: str, message_2: str) -> Dict[str, Any]:
        with Store(settings.config().db) as store:
            lead = store.get(lead_id)
            if lead is None:
                return {"ok": False, "error": "Лид не найден"}
            status = lead.status
            if status == KEPT and (message_1.strip() or message_2.strip()):
                status = WRITTEN
            store.update_fields(lead_id, message_1=message_1, message_2=message_2,
                                status=status)
            return {"ok": True, "lead": self._lead_dict(store.get(lead_id))}

    @guard
    def lead_flag(self, lead_id: str, action: str) -> Dict[str, Any]:
        with Store(settings.config().db) as store:
            lead = store.get(lead_id)
            if lead is None:
                return {"ok": False, "error": "Лид не найден"}
            if action == "sent":
                store.update_fields(lead_id,
                                    sent_at=datetime.now().isoformat(timespec="seconds"))
            elif action == "unsent":
                store.update_fields(lead_id, sent_at="")
            elif action == "drop":
                store.update_fields(lead_id, status=DROPPED, ai_reason="убран вручную")
            elif action == "restore":
                store.update_fields(lead_id,
                                    status=WRITTEN if lead.message_1 else KEPT,
                                    ai_reason="возвращён вручную")
            else:
                return {"ok": False, "error": "Неизвестное действие"}
            return {"ok": True, "lead": self._lead_dict(store.get(lead_id)),
                    "counts": self._counts()}

    # ------------------------------------------------------------ сообщения

    @guard
    def messages_get(self) -> Dict[str, Any]:
        return {"ok": True, "messages": settings.read_messages()}

    @guard
    def messages_save(self, data: Dict[str, Any]) -> Dict[str, Any]:
        current = settings.read_messages()
        current.update(data or {})
        if (current.get("mode") or "") == "template":
            missing = [name for key, name in (("message_1", "первого"), ("message_2", "второго"))
                       if not (current.get(key) or {}).get("template")]
            if missing:
                return {"ok": False,
                        "error": "Без ИИ шаблон обязателен, а он пуст у %s сообщения."
                                 % " и ".join(missing)}
        settings.save_messages(current)
        return {"ok": True}

    @guard
    def messages_restore(self) -> Dict[str, Any]:
        example = settings.read_yaml(settings.MESSAGES_EXAMPLE)
        if not example:
            return {"ok": False, "error": "Файл messages.example.yaml не найден"}
        settings.save_messages(example)
        return {"ok": True, "messages": example}

    # ------------------------------------------------------------ настройки

    @guard
    def settings_get(self) -> Dict[str, Any]:
        config = settings.read_config()
        return {
            "ok": True,
            "api_key": settings.api_key(),
            "model": (config.get("gemini") or {}).get("model", "gemini-2.5-flash"),
            "rpm": (config.get("gemini") or {}).get("rpm", 12),
            "batch_size": (config.get("gemini") or {}).get("batch_size", 12),
            "min_score": (config.get("filter") or {}).get("min_score", 55),
            "rules": (config.get("filter") or {}).get("rules", ""),
            "region": (config.get("verify") or {}).get("region", "UA"),
        }

    @guard
    def settings_save(self, data: Dict[str, Any]) -> Dict[str, Any]:
        data = data or {}
        if data.get("api_key") is not None:
            settings.save_api_key(str(data["api_key"]).strip())

        config = settings.read_config()
        gemini = config.setdefault("gemini", {})
        if data.get("model"):
            gemini["model"] = str(data["model"]).strip()
        for key in ("rpm", "batch_size"):
            if data.get(key):
                try:
                    gemini[key] = int(data[key])
                except (TypeError, ValueError):
                    pass

        filters = config.setdefault("filter", {})
        if data.get("min_score") is not None:
            try:
                filters["min_score"] = int(data["min_score"])
            except (TypeError, ValueError):
                pass
        if data.get("rules") is not None:
            filters["rules"] = str(data["rules"])

        if data.get("region"):
            config.setdefault("verify", {})["region"] = str(data["region"]).strip().upper()

        settings.save_config(config)
        return {"ok": True, "has_key": bool(settings.api_key())}

    # ------------------------------------------------------------- действия

    @guard
    def open_external(self, url: str) -> Dict[str, Any]:
        if not url:
            return {"ok": False, "error": "Пустая ссылка"}
        webbrowser.open(url)
        return {"ok": True}

    @guard
    def export(self, kind: str, status: Optional[str] = None) -> Dict[str, Any]:
        config = settings.config()
        with Store(config.db) as store:
            items = store.fetch(status=status or None)
        if not items:
            return {"ok": False, "error": "Нечего выгружать"}

        export_dir = config.get("export_dir", os.path.join(settings.ROOT, "exports"))
        os.makedirs(export_dir, exist_ok=True)
        base = os.path.join(export_dir, "leads-%s" % datetime.now().strftime("%Y%m%d-%H%M"))
        if kind == "csv":
            path = export_csv(items, base + ".csv")
        else:
            path = export_html(items, base + ".html", title="Лиды: %s" % (config.niche or ""))
        reveal(path)
        return {"ok": True, "path": os.path.basename(path), "count": len(items)}


def reveal(path: str) -> None:
    """Показать готовый файл в файловом менеджере. Команда у каждой ОС своя."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        if sys.platform == "darwin":
            subprocess.call(["open", "-R", path])
        elif os.name == "nt":
            subprocess.call(["explorer", "/select,", os.path.normpath(path)])
        else:
            subprocess.call(["xdg-open", folder])
    except Exception:
        # не показали папку, но файл на месте: молчим, это не повод рушить выгрузку
        pass
