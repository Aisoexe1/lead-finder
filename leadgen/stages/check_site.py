"""Проверка, есть ли у бизнеса сайт на самом деле.

В карточке OSM поле website заполняют редко, поэтому «нет сайта» там означает
лишь «не записан». Писать владельцу «у вас нет сайта», когда сайт есть, это
худшее начало разговора, поэтому перед отбором спрашиваем у модели с доступом
к поиску: найдётся ли что-то по названию и городу.

Соцсети за сайт не считаются: профиль в Instagram это как раз повод предложить
сайт, а не причина отказаться от лида.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from ..gemini import Gemini, GeminiError
from ..models import DROPPED, VERIFIED, Lead
from ..storage import Store

SYSTEM = """Ты проверяешь, есть ли у небольшого локального бизнеса собственный сайт.

Для каждой записи выполни поиск и реши:
- "site" — нашёлся собственный сайт этого бизнеса: домен с его названием,
  страница с услугами, ценами, записью. Укажи адрес в поле url;
- "social" — нашлись только профили в Instagram, Facebook, TikTok, Telegram,
  страница в каталоге, на маркетплейсе или в агрегаторе вроде booksy. Своего
  сайта нет;
- "none" — ничего похожего не нашлось;
- "unsure" — нашлось что-то похожее, но не уверен, что это тот же бизнес
  (совпало название, но другой город или другая сфера).

Это разные компании с похожими названиями: сверяй город и род занятий.
Сетевой бренд с общим сайтом это "site". Каталог, где бизнес просто упомянут,
это "social".

Отвечай только JSON-массивом без пояснений, по объекту на запись:
[{"n": 1, "verdict": "none", "url": "", "why": "коротко"}]"""


def _prompt(batch: List[Lead]) -> str:
    items = []
    for index, lead in enumerate(batch, start=1):
        items.append({
            "n": index,
            "name": lead.name,
            "city": lead.city,
            "address": lead.address,
            "category": lead.category,
        })
    return (
        "Проверь каждую запись поиском и верни JSON-массив той же длины.\n\n"
        + json.dumps(items, ensure_ascii=False, indent=1)
    )


def run_check_site(
    store: Store,
    config,
    client: Gemini,
    limit: int = 0,
    verbose: bool = True,
) -> Dict[str, int]:
    """Отсеивает тех, у кого сайт нашёлся. Остальные идут дальше по конвейеру."""
    batch_size = max(1, int(config.get("gemini.batch_size", 12)) // 2)
    leads = store.fetch(status=VERIFIED, limit=limit or None)
    if not leads:
        return {"checked": 0, "dropped": 0}

    if not client.search_available():
        if verbose:
            print("  поиск в вебе недоступен для этой модели, шаг пропущен")
            print("  попробуй gemini-2.5-flash или gemini-2.5-pro в настройках")
        return {"checked": 0, "dropped": 0, "skipped": True}

    checked = dropped = unsure = failed = 0

    for start in range(0, len(leads), batch_size):
        batch = leads[start:start + batch_size]
        if verbose:
            print("  батч %d-%d из %d" % (start + 1, start + len(batch), len(leads)))

        try:
            results = client.generate_json(
                _prompt(batch), {}, system=SYSTEM,
                temperature=0.1,  # здесь нужен факт, а не фантазия
                max_output_tokens=4096,
                search=True,
            )
        except GeminiError as exc:
            print("  батч не проверился: %s" % exc)
            failed += len(batch)
            continue

        by_index = {}
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and isinstance(item.get("n"), int):
                    by_index[item["n"]] = item

        for index, lead in enumerate(batch, start=1):
            answer = by_index.get(index)
            if answer is None:
                failed += 1
                continue

            verdict = (answer.get("verdict") or "").strip()
            url = (answer.get("url") or "").strip()
            why = (answer.get("why") or "").strip()[:200]
            checked += 1

            if verdict == "site":
                store.update_fields(
                    lead.id, status=DROPPED, website=url or lead.website,
                    website_status="found_in_search",
                    ai_reason="сайт нашёлся в поиске: %s" % (url or why),
                )
                dropped += 1
            elif verdict == "unsure":
                unsure += 1
                store.update_fields(lead.id, ai_reason="сайт под вопросом: %s" % why)
            else:
                store.update_fields(
                    lead.id,
                    website_status="absent",
                    ai_reason=("только соцсети" if verdict == "social" else ""),
                )

    if verbose:
        print("  проверено: %d, сайт нашёлся у %d" % (checked, dropped))
        if unsure:
            print("  под вопросом: %d (остались в списке, помечены)" % unsure)
        if failed:
            print("  не проверилось: %d" % failed)
        print("  %s" % client.usage_line())
    return {"checked": checked, "dropped": dropped, "unsure": unsure, "failed": failed}
