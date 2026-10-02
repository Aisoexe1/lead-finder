"""Шаги конвейера в виде задач для интерфейса."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from leadgen.enrich import run_verify
from leadgen.gemini import build_client
from leadgen.models import KEPT, VERIFIED, WRITTEN
from leadgen.sources import registry
from leadgen.stages.check_site import run_check_site
from leadgen.stages.filter import run_filter
from leadgen.stages.write import run_write
from leadgen.storage import Store

from . import settings
from .tasks import Reporter


def scrape(rep: Reporter, niche: str, cities: List[str], limit: int,
           sources: List[str]) -> Dict[str, Any]:
    cfg = settings.config()
    available = registry()
    chosen = [
        (name, cls, cfg.get("sources.%s" % name, {}) or {})
        for name, cls in available.items()
        if name in sources
    ]
    if not chosen:
        rep.log("не выбран ни один источник")
        return {}

    rep.log("ниша: %s" % niche)
    rep.log("города: %s" % ", ".join(cities))
    rep.log("источники: %s" % ", ".join(n for n, _, _ in chosen))

    new = merged = 0
    with Store(cfg.db) as store:
        for city in cities:
            rep.log("")
            rep.log("=== %s ===" % city)
            for name, cls, opts in chosen:
                source = cls(opts)
                try:
                    with rep.capture_stdout():
                        for lead in source.search(niche, city, limit):
                            if store.upsert(lead) == "new":
                                new += 1
                            else:
                                merged += 1
                except Exception as exc:
                    rep.log("  [%s] сорвался: %s" % (name, exc))
    rep.log("")
    rep.log("новых: %d, склеено с существующими: %d" % (new, merged))
    return {"new": new, "merged": merged}


def verify(rep: Reporter) -> Dict[str, Any]:
    cfg = settings.config()
    with Store(cfg.db) as store:
        with rep.capture_stdout():
            kept, dropped = run_verify(store, cfg)
    return {"kept": kept, "dropped": dropped}


def check_sites(rep: Reporter) -> Dict[str, Any]:
    cfg = settings.config()
    client = build_client(cfg)
    with Store(cfg.db) as store:
        pending = len(store.fetch(status=VERIFIED))
        rep.log("на входе: %d" % pending)
        if pending == 0:
            rep.log("нечего проверять, сначала поиск и проверка контактов")
            return {}
        with rep.capture_stdout():
            result = run_check_site(store, cfg, client)
    return result or {}


def ai_filter(rep: Reporter) -> Dict[str, Any]:
    cfg = settings.config()
    client = build_client(cfg)
    with Store(cfg.db) as store:
        pending = len(store.fetch(status=VERIFIED))
        rep.log("на входе: %d" % pending)
        if pending == 0:
            rep.log("нечего отсеивать, сначала поиск и проверка")
            return {}
        with rep.capture_stdout():
            result = run_filter(store, cfg, client)
    return result or {}


def write_messages(rep: Reporter, rewrite: bool = False,
                   lead_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    cfg = settings.config()
    messages = settings.read_messages()
    mode = (messages.get("mode") or "ai").lower()
    rep.log("режим: %s" % mode)
    client = build_client(cfg) if mode != "template" else None
    with Store(cfg.db) as store:
        if lead_ids:
            rep.log("переписываю выбранных: %d" % len(lead_ids))
        else:
            statuses = [KEPT, WRITTEN] if rewrite else [KEPT]
            pending = len(store.fetch(statuses=statuses))
            rep.log("на входе: %d" % pending)
            if pending == 0:
                rep.log("нет отобранных лидов, сначала отсев")
                return {}
        with rep.capture_stdout():
            result = run_write(store, cfg, messages, client, rewrite=rewrite,
                               lead_ids=lead_ids)
    return result or {}


def full_run(rep: Reporter, niche: str, cities: List[str], limit: int,
             sources: List[str]) -> Dict[str, Any]:
    rep.log("### 1. поиск")
    scrape(rep, niche, cities, limit, sources)
    rep.log("")
    rep.log("### 2. проверка контактов")
    verify(rep)
    rep.log("")
    if settings.read_config().get("verify", {}).get("search_sites", True):
        rep.log("### 3. проверка, нет ли сайта на самом деле")
        check_sites(rep)
        rep.log("")
    rep.log("### 4. отсев мусора через Gemini")
    ai_filter(rep)
    rep.log("")
    rep.log("### 5. сообщения")
    result = write_messages(rep)
    rep.log("")
    rep.log("готово")
    return result


def check_key(rep: Reporter) -> Dict[str, Any]:
    """Быстрая проверка, что ключ рабочий и модель отвечает."""
    cfg = settings.config()
    client = build_client(cfg, verbose=False)
    rep.log("модель: %s" % cfg.get("gemini.model"))
    answer = client.generate("Ответь одним словом: работает", max_output_tokens=2048)
    rep.log("ответ: %s" % answer.strip()[:120])
    rep.log("ключ рабочий")
    return {"ok": True}
