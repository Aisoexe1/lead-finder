from __future__ import annotations

import argparse
import os
import shutil
import sys
import webbrowser
from datetime import datetime
from typing import List, Optional

from .config import Config, load_messages
from .export import export_csv, export_html
from .gemini import GeminiError, build_client
from .models import DROPPED, KEPT, NEW, VERIFIED, WRITTEN
from .sources import registry
from .storage import Store


def _banner(text: str) -> None:
    print("\n=== %s ===" % text)


# ------------------------------------------------------------------- команды

def cmd_init(args) -> int:
    pairs = [("config.example.yaml", "config.yaml"), ("messages.example.yaml", "messages.yaml")]
    if not os.path.exists(".env") and os.path.exists(".env.example"):
        pairs.append((".env.example", ".env"))
    for source, target in pairs:
        if os.path.exists(target):
            print("%s уже есть, не трогаю" % target)
            continue
        if not os.path.exists(source):
            print("нет файла-образца %s" % source)
            continue
        shutil.copy(source, target)
        print("создан %s" % target)
    print("\nДальше: впиши GEMINI_API_KEY в .env, нишу и города в config.yaml,")
    print("свои тексты в messages.yaml. Потом: python -m leadgen run")
    return 0


def cmd_scrape(args) -> int:
    config = Config.load(args.config)
    cities: List[str] = args.city or config.cities
    niche: str = args.niche or config.niche
    if not cities:
        print("Не заданы города: укажи search.cities в конфиге или --city")
        return 1
    if not niche:
        print("Не задана ниша: укажи search.niche в конфиге или --niche")
        return 1

    limit = args.limit or int(config.get("search.limit_per_city", 200))
    available = registry()
    enabled = []
    for name, cls in available.items():
        settings = config.get("sources.%s" % name, {}) or {}
        if args.source:
            if name in args.source:
                enabled.append((name, cls, settings))
        elif settings.get("enabled"):
            enabled.append((name, cls, settings))

    if not enabled:
        print("Ни один источник не включён (sources.*.enabled в конфиге)")
        return 1

    print("Ниша: %s" % niche)
    print("Города: %s" % ", ".join(cities))
    print("Источники: %s" % ", ".join(n for n, _, _ in enabled))

    total_new = total_merged = 0
    with Store(config.db) as store:
        for city in cities:
            _banner(city)
            for name, cls, settings in enabled:
                source = cls(settings)
                try:
                    for lead in source.search(niche, city, limit):
                        result = store.upsert(lead)
                        if result == "new":
                            total_new += 1
                        else:
                            total_merged += 1
                except KeyboardInterrupt:
                    print("\nпрервано, собранное сохранено")
                    return 130
                except Exception as exc:
                    print("  [%s] сорвался: %s" % (name, exc))

    print("\nНовых лидов: %d, склеено с существующими: %d" % (total_new, total_merged))
    print("Дальше: python -m leadgen verify")
    return 0


def cmd_verify(args) -> int:
    from .enrich import run_verify

    config = Config.load(args.config)
    _banner("проверка контактов и сайтов")
    with Store(config.db) as store:
        kept, dropped = run_verify(store, config)
    if kept == 0 and dropped == 0:
        print("  нечего проверять: нет лидов со статусом new")
    else:
        print("Дальше: python -m leadgen filter")
    return 0


def cmd_check_sites(args) -> int:
    from .stages.check_site import run_check_site

    config = Config.load(args.config)
    _banner("проверка, нет ли сайта на самом деле")
    try:
        client = build_client(config)
    except RuntimeError as exc:
        print(exc)
        return 1

    with Store(config.db) as store:
        pending = len(store.fetch(VERIFIED))
        if pending == 0:
            print("  нет проверенных лидов, сначала verify")
            return 0
        print("  на входе: %d" % pending)
        try:
            run_check_site(store, config, client, limit=args.limit or 0)
        except GeminiError as exc:
            print("Gemini: %s" % exc)
            return 1
    print("Дальше: python -m leadgen filter")
    return 0


def cmd_filter(args) -> int:
    from .stages.filter import run_filter

    config = Config.load(args.config)
    _banner("отсев мусора через Gemini")
    try:
        client = build_client(config)
    except RuntimeError as exc:
        print(exc)
        return 1

    with Store(config.db) as store:
        pending = len(store.fetch(status=VERIFIED))
        if pending == 0:
            print("  нет проверенных лидов, сначала verify")
            return 0
        print("  на входе: %d" % pending)
        try:
            run_filter(store, config, client, limit=args.limit or 0)
        except GeminiError as exc:
            print("Gemini: %s" % exc)
            return 1
    print("Дальше: python -m leadgen write")
    return 0


def cmd_write(args) -> int:
    from .stages.write import run_write

    config = Config.load(args.config)
    messages = load_messages(args.messages or config.messages_path)
    mode = (messages.get("mode") or "ai").lower()
    _banner("сообщения (режим: %s)" % mode)

    client = None
    if mode != "template":
        try:
            client = build_client(config)
        except RuntimeError as exc:
            print(exc)
            return 1

    with Store(config.db) as store:
        statuses = [KEPT, WRITTEN] if args.rewrite else [KEPT]
        pending = len(store.fetch(statuses=statuses))
        if pending == 0:
            print("  нет отобранных лидов, сначала filter")
            return 0
        print("  на входе: %d" % pending)
        try:
            run_write(
                store, config, messages, client,
                limit=args.limit or 0, rewrite=args.rewrite,
            )
        except GeminiError as exc:
            print("Gemini: %s" % exc)
            return 1
    print("Дальше: python -m leadgen export")
    return 0


def cmd_export(args) -> int:
    config = Config.load(args.config)
    export_dir = args.out or config.get("export_dir", "exports")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    statuses = [WRITTEN] if not args.all else None

    with Store(config.db) as store:
        leads = store.fetch(statuses=statuses, city=args.city)
    if not leads:
        print("Нечего выгружать")
        return 0

    base = os.path.join(export_dir, "leads-%s" % stamp)
    csv_path = export_csv(leads, base + ".csv")
    html_path = export_html(
        leads,
        base + ".html",
        title="Лиды: %s" % (config.niche or "без ниши"),
        subject=args.subject,
    )
    print("Выгружено %d лидов:" % len(leads))
    print("  %s" % csv_path)
    print("  %s" % html_path)
    if args.open:
        webbrowser.open("file://" + os.path.abspath(html_path))
    return 0


def cmd_run(args) -> int:
    """Весь конвейер за один запуск."""
    steps = [cmd_scrape, cmd_verify]
    if Config.load(args.config).get("verify.search_sites", True):
        steps.append(cmd_check_sites)
    steps += [cmd_filter, cmd_write, cmd_export]
    for step in steps:
        code = step(args)
        if code not in (0, None):
            return code
    return 0


def cmd_stats(args) -> int:
    config = Config.load(args.config)
    with Store(config.db) as store:
        stats = store.stats()
    labels = {
        NEW: "собрано, не проверено", VERIFIED: "проверено, ждёт отсева",
        KEPT: "отобрано Gemini", DROPPED: "отсеяно", WRITTEN: "с сообщениями",
    }
    print("Всего лидов: %d (с телефоном: %d)" % (stats["total"], stats["with_phone"]))
    print("\nПо статусам:")
    for status, count in sorted(stats["by_status"].items(), key=lambda kv: -kv[1]):
        print("  %-24s %d" % (labels.get(status, status), count))
    print("\nПо источникам:")
    for source, count in list(stats["by_source"].items())[:10]:
        print("  %-24s %d" % (source or "?", count))
    print("\nПо городам:")
    for city, count in list(stats["by_city"].items())[:10]:
        print("  %-24s %d" % (city or "?", count))
    return 0


def cmd_show(args) -> int:
    config = Config.load(args.config)
    with Store(config.db) as store:
        leads = store.fetch(status=args.status, limit=args.limit)
    for lead in leads:
        print("\n" + "-" * 60)
        print("%s · %s · оценка %s" % (lead.name, lead.city, lead.ai_score))
        print("контакт: %s %s %s" % (lead.phone_e164, lead.email, lead.instagram))
        if lead.ai_reason:
            print("почему: %s" % lead.ai_reason)
        if lead.message_1:
            print("\n[1] %s" % lead.message_1)
        if lead.message_2:
            print("\n[2] %s" % lead.message_2)
    print("\nпоказано: %d" % len(leads))
    return 0


# --------------------------------------------------------------------- разбор

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leadgen",
        description="Поиск бизнесов без сайта и подготовка первых сообщений.",
    )
    parser.add_argument("--config", default="config.yaml", help="путь к конфигу")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="создать config.yaml и messages.yaml").set_defaults(func=cmd_init)

    scrape = subparsers.add_parser("scrape", help="собрать лиды из источников")
    scrape.add_argument("--niche", help="ниша, перекрывает конфиг")
    scrape.add_argument("--city", action="append", help="город, можно повторять")
    scrape.add_argument("--source", action="append", help="только этот источник")
    scrape.add_argument("--limit", type=int, help="сколько лидов на город")
    scrape.set_defaults(func=cmd_scrape)

    verify = subparsers.add_parser("verify", help="нормализовать телефоны, проверить сайты")
    verify.set_defaults(func=cmd_verify)

    sites = subparsers.add_parser("check-sites",
                                  help="проверить поиском, нет ли у лида сайта")
    sites.add_argument("--limit", type=int)
    sites.set_defaults(func=cmd_check_sites)

    filt = subparsers.add_parser("filter", help="отсев мусора через Gemini")
    filt.add_argument("--limit", type=int, help="обработать не больше N лидов")
    filt.set_defaults(func=cmd_filter)

    write = subparsers.add_parser("write", help="написать по два сообщения")
    write.add_argument("--messages", help="путь к файлу шаблонов")
    write.add_argument("--limit", type=int)
    write.add_argument("--rewrite", action="store_true", help="перегенерировать уже написанные")
    write.set_defaults(func=cmd_write)

    export = subparsers.add_parser("export", help="выгрузить CSV и HTML")
    export.add_argument("--out", help="папка для выгрузки")
    export.add_argument("--city")
    export.add_argument("--all", action="store_true", help="выгрузить все статусы")
    export.add_argument("--open", action="store_true", help="открыть HTML после выгрузки")
    export.add_argument("--subject", default="Сайт для вашего бизнеса", help="тема письма")
    export.set_defaults(func=cmd_export)

    run = subparsers.add_parser("run", help="весь конвейер разом")
    for sub in (run,):
        sub.add_argument("--niche")
        sub.add_argument("--city", action="append")
        sub.add_argument("--source", action="append")
        sub.add_argument("--limit", type=int)
        sub.add_argument("--messages")
        sub.add_argument("--out")
        sub.add_argument("--all", action="store_true")
        sub.add_argument("--open", action="store_true")
        sub.add_argument("--rewrite", action="store_true")
        sub.add_argument("--subject", default="Сайт для вашего бизнеса")
    run.set_defaults(func=cmd_run)

    subparsers.add_parser("stats", help="что сейчас в базе").set_defaults(func=cmd_stats)

    show = subparsers.add_parser("show", help="показать лиды в терминале")
    show.add_argument("--status", default=WRITTEN)
    show.add_argument("--limit", type=int, default=5)
    show.set_defaults(func=cmd_show)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    # у подкоманд без этих флагов run всё равно к ним обращается
    for attr, default in (("city", None), ("niche", None), ("source", None),
                          ("limit", None), ("messages", None), ("out", None),
                          ("all", False), ("open", False), ("rewrite", False),
                          ("subject", "Сайт для вашего бизнеса"), ("status", WRITTEN)):
        if not hasattr(args, attr):
            setattr(args, attr, default)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    except KeyboardInterrupt:
        print("\nпрервано")
        return 130


if __name__ == "__main__":
    sys.exit(main())
