from __future__ import annotations

import json
from typing import Any, Dict, List

from ..gemini import Gemini, GeminiError
from ..models import DROPPED, KEPT, Lead
from ..storage import Store

SYSTEM = """Ты помогаешь веб-студии отбирать холодные лиды: малый бизнес, которому
имеет смысл предложить сайт.

Список собран скрапером из открытых карт, каталогов и поисковой выдачи, поэтому
в нём есть мусор. Твоя работа — отсеять мусор, а не продать услугу.

Отсеивай (verdict = drop):
- сетевые бренды, франшизы и крупные компании: у них сайт почти наверняка есть,
  а решение принимает не локальный менеджер;
- государственные учреждения, школы, больницы, почту, банки;
- объекты, которые не являются бизнесом: банкоматы, остановки, парковки,
  подъезды, гаражи, жилые дома, точки на карте без реального заведения;
- записи с мусорным названием: одна буква, набор цифр, "test", "б/н",
  название улицы вместо названия фирмы;
- дубликаты по смыслу: то же заведение, что уже в списке, другими словами;
- бизнес, которому сайт объективно не нужен: киоск, ларёк, точка на рынке,
  вендинговый автомат;
- явные признаки того, что сайт уже есть.

Оставляй (verdict = keep):
- локальный малый и средний бизнес, который продаёт услуги или товары людям,
  где сайт даёт запись, каталог, цены, портфолио или заявки.

score от 0 до 100 — насколько это перспективный клиент именно для заказа сайта.
Учитывай: понятная ниша, живой бизнес, есть контакт, сайта нет, есть что
показывать на сайте. reason — одна короткая фраза на русском, по делу."""

SCHEMA: Dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "n": {"type": "integer"},
            "verdict": {"type": "string", "enum": ["keep", "drop"]},
            "score": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["n", "verdict", "score", "reason"],
    },
}


def _prompt(batch: List[Lead], niche: str, rules: str) -> str:
    items = []
    for index, lead in enumerate(batch, start=1):
        context = lead.context_for_ai()
        context["n"] = index
        context.pop("id", None)
        items.append(context)

    parts = ["Ниша, которую ищем: %s" % (niche or "не задана")]
    if rules:
        parts.append(
            "Дополнительные правила от владельца студии, они важнее общих:\n%s" % rules
        )
    parts.append(
        "Оцени каждую запись. Верни JSON-массив, по одному объекту на запись, "
        "поле n должно совпадать с номером записи. Ничего не пропускай."
    )
    parts.append(json.dumps(items, ensure_ascii=False, indent=1))
    return "\n\n".join(parts)


def run_filter(
    store: Store,
    config,
    client: Gemini,
    limit: int = 0,
    verbose: bool = True,
) -> Dict[str, int]:
    """Прогоняет проверенные лиды через Gemini и размечает keep/drop."""
    batch_size = int(config.get("gemini.batch_size", 12))
    min_score = int(config.get("filter.min_score", 55))
    rules = (config.get("filter.rules") or "").strip()
    niche = config.niche

    leads = store.fetch(status="verified", limit=limit or None)
    if not leads:
        return {"kept": 0, "dropped": 0, "total": 0}

    kept = dropped = failed = 0

    for start in range(0, len(leads), batch_size):
        batch = leads[start:start + batch_size]
        if verbose:
            print("  батч %d-%d из %d" % (start + 1, start + len(batch), len(leads)))

        try:
            verdicts = client.generate_json(
                _prompt(batch, niche, rules),
                SCHEMA,
                system=SYSTEM,
                temperature=0.2,  # отбор должен быть стабильным, не творческим
                max_output_tokens=4096,
            )
        except GeminiError as exc:
            print("  ошибка на батче, лиды остались непроверенными: %s" % exc)
            failed += len(batch)
            continue

        by_index = {}
        if isinstance(verdicts, list):
            for item in verdicts:
                if isinstance(item, dict) and isinstance(item.get("n"), int):
                    by_index[item["n"]] = item

        for index, lead in enumerate(batch, start=1):
            verdict = by_index.get(index)
            if verdict is None:
                # модель пропустила запись: оставляем на ручную проверку, не удаляем
                store.update_fields(
                    lead.id, ai_reason="Gemini не вернул вердикт", ai_score=None
                )
                failed += 1
                continue

            score = int(verdict.get("score") or 0)
            reason = (verdict.get("reason") or "").strip()[:300]
            keep = verdict.get("verdict") == "keep" and score >= min_score

            store.update_fields(
                lead.id,
                ai_score=score,
                ai_verdict="keep" if keep else "drop",
                ai_reason=reason,
                status=KEPT if keep else DROPPED,
            )
            if keep:
                kept += 1
            else:
                dropped += 1

    if verbose:
        print("  оставлено: %d, отсеяно: %d, без вердикта: %d" % (kept, dropped, failed))
        print("  %s" % client.usage_line())
    return {"kept": kept, "dropped": dropped, "failed": failed, "total": len(leads)}
