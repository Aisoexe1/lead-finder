from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ..gemini import Gemini, GeminiError
from ..models import KEPT, WRITTEN, Lead
from ..storage import Store

SCHEMA: Dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "n": {"type": "integer"},
            "message_1": {"type": "string"},
            "message_2": {"type": "string"},
        },
        "required": ["n", "message_1", "message_2"],
    },
}

SYSTEM_BASE = """Ты пишешь первые холодные сообщения от небольшой веб-студии
локальному бизнесу, у которого нет сайта.

Жёсткие правила:
- пиши так, как пишет живой человек в мессенджере, а не рассылка;
- обращайся к конкретному бизнесу: покажи, что смотрел именно на него;
- никакого давления, срочности, "ограниченное предложение", "успейте";
- не выдумывай факты о клиенте, которых нет во входных данных: не приписывай
  ему количество филиалов, годы работы, отзывы или оборот;
- не обещай конкретных цифр роста продаж;
- два сообщения должны быть разными по смыслу, а не пересказом друг друга:
  первое — знакомство и повод, второе — вежливое напоминание позже;
- второе сообщение пишется так, будто на первое не ответили.

Верни JSON-массив: по объекту на каждую запись, поле n совпадает с номером."""


class SafeDict(dict):
    """Неизвестный плейсхолдер оставляем как есть, чтобы шаблон не падал."""

    def __missing__(self, key: str) -> str:
        return "{%s}" % key


def placeholders(lead: Lead, messages: Dict[str, Any], niche: str) -> Dict[str, str]:
    sender = messages.get("sender") or {}
    values = {
        "name": lead.name,
        "city": lead.city,
        "category": lead.category,
        "address": lead.address,
        "phone": lead.phone_e164 or lead.phone,
        "niche": niche,
    }
    for key, value in sender.items():
        values["sender_%s" % key] = str(value)
        values.setdefault(key, str(value))
    return values


def render_template(template: str, lead: Lead, messages: Dict[str, Any], niche: str) -> str:
    return (template or "").format_map(SafeDict(placeholders(lead, messages, niche))).strip()


# --------------------------------------------------------------------- правила

def style_block(messages: Dict[str, Any]) -> str:
    style = messages.get("style") or {}
    lines: List[str] = []
    language = messages.get("language")
    if language:
        lines.append("Язык сообщений: %s. Пиши только на нём." % language)
    if style.get("tone"):
        lines.append("Тон: %s" % style["tone"])
    if style.get("max_chars"):
        lines.append("Максимум %s символов в каждом сообщении." % style["max_chars"])
    if style.get("no_emoji"):
        lines.append("Эмодзи запрещены.")
    if style.get("no_dashes"):
        lines.append("Не используй длинное тире.")
    forbidden = style.get("forbid") or []
    if forbidden:
        lines.append("Запрещённые слова и штампы: %s" % ", ".join(map(str, forbidden)))
    if style.get("extra"):
        lines.append(str(style["extra"]))
    return "\n".join(lines)


def sender_block(messages: Dict[str, Any]) -> str:
    sender = messages.get("sender") or {}
    if not sender:
        return ""
    pairs = ["%s: %s" % (k, v) for k, v in sender.items()]
    return "Кто пишет (используй эти факты, других о себе не выдумывай):\n" + "\n".join(pairs)


def _message_spec(messages: Dict[str, Any], key: str, mode: str) -> str:
    block = messages.get(key) or {}
    lines = []
    if block.get("goal"):
        lines.append("Задача: %s" % block["goal"])
    if mode == "hybrid" and block.get("template"):
        lines.append(
            "Опирайся на этот шаблон, сохрани его структуру и смысл, "
            "подставь и адаптируй детали под конкретный бизнес:\n%s" % block["template"]
        )
    examples = block.get("examples") or []
    if examples:
        lines.append(
            "Примеры нужной интонации (не копируй дословно):\n"
            + "\n".join("- %s" % e for e in examples)
        )
    return "\n".join(lines)


def build_system(messages: Dict[str, Any], mode: str) -> str:
    parts = [SYSTEM_BASE]
    sender = sender_block(messages)
    if sender:
        parts.append(sender)
    style = style_block(messages)
    if style:
        parts.append("Требования к стилю:\n" + style)
    spec_1 = _message_spec(messages, "message_1", mode)
    if spec_1:
        parts.append("СООБЩЕНИЕ 1.\n" + spec_1)
    spec_2 = _message_spec(messages, "message_2", mode)
    if spec_2:
        parts.append("СООБЩЕНИЕ 2.\n" + spec_2)
    if messages.get("extra_instructions"):
        parts.append(str(messages["extra_instructions"]))
    return "\n\n".join(parts)


def _prompt(batch: List[Lead], niche: str) -> str:
    items = []
    for index, lead in enumerate(batch, start=1):
        context = lead.context_for_ai()
        context.pop("id", None)
        context["n"] = index
        if lead.ai_reason:
            context["why_selected"] = lead.ai_reason
        items.append(context)
    return (
        "Ниша: %s\n\nНапиши по два сообщения для каждого бизнеса из списка.\n\n%s"
        % (niche or "не задана", json.dumps(items, ensure_ascii=False, indent=1))
    )


# ------------------------------------------------------------------ проверки

def violations(text: str, messages: Dict[str, Any]) -> List[str]:
    style = messages.get("style") or {}
    problems = []
    max_chars = style.get("max_chars")
    if max_chars and len(text) > int(max_chars) * 1.15:
        problems.append("длиннее лимита (%d символов)" % len(text))
    if style.get("no_emoji") and re.search(
        "[\U0001F300-\U0001FAFF☀-➿]", text
    ):
        problems.append("есть эмодзи")
    if style.get("no_dashes") and "—" in text:
        problems.append("есть длинное тире")
    for word in style.get("forbid") or []:
        if str(word).lower() in text.lower():
            problems.append("запрещённая фраза: %s" % word)
    if "{" in text and "}" in text:
        problems.append("остался незаполненный плейсхолдер")
    return problems


# --------------------------------------------------------------------- запуск

def run_write(
    store: Store,
    config,
    messages: Dict[str, Any],
    client: Optional[Gemini] = None,
    limit: int = 0,
    rewrite: bool = False,
    verbose: bool = True,
) -> Dict[str, int]:
    mode = (messages.get("mode") or "ai").lower()
    niche = config.niche
    batch_size = int(config.get("gemini.batch_size", 12))

    statuses = [KEPT, WRITTEN] if rewrite else [KEPT]
    leads = store.fetch(statuses=statuses, limit=limit or None)
    if not leads:
        return {"written": 0, "failed": 0}

    # чистая подстановка шаблона, Gemini не нужен
    if mode == "template":
        written = 0
        for lead in leads:
            msg_1 = render_template(
                (messages.get("message_1") or {}).get("template", ""), lead, messages, niche
            )
            msg_2 = render_template(
                (messages.get("message_2") or {}).get("template", ""), lead, messages, niche
            )
            store.update_fields(
                lead.id, message_1=msg_1, message_2=msg_2, status=WRITTEN
            )
            written += 1
        if verbose:
            print("  шаблоны подставлены: %d (без обращений к Gemini)" % written)
        return {"written": written, "failed": 0}

    if client is None:
        raise RuntimeError("для режима '%s' нужен клиент Gemini" % mode)

    system = build_system(messages, mode)
    written = failed = flagged = 0

    for start in range(0, len(leads), batch_size):
        batch = leads[start:start + batch_size]
        if verbose:
            print("  батч %d-%d из %d" % (start + 1, start + len(batch), len(leads)))
        try:
            results = client.generate_json(
                _prompt(batch, niche),
                SCHEMA,
                system=system,
                max_output_tokens=8192,
            )
        except GeminiError as exc:
            print("  ошибка на батче: %s" % exc)
            failed += len(batch)
            continue

        by_index = {}
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and isinstance(item.get("n"), int):
                    by_index[item["n"]] = item

        for index, lead in enumerate(batch, start=1):
            result = by_index.get(index)
            if result is None:
                failed += 1
                continue
            msg_1 = (result.get("message_1") or "").strip()
            msg_2 = (result.get("message_2") or "").strip()
            if not msg_1 or not msg_2:
                failed += 1
                continue

            problems = violations(msg_1, messages) + violations(msg_2, messages)
            note = lead.ai_reason
            if problems:
                flagged += 1
                note = "%s | стиль: %s" % (note, "; ".join(sorted(set(problems))))

            store.update_fields(
                lead.id,
                message_1=msg_1,
                message_2=msg_2,
                ai_reason=note[:300],
                status=WRITTEN,
            )
            written += 1

    if verbose:
        print("  написано: %d, не получилось: %d" % (written, failed))
        if flagged:
            print("  с замечаниями по стилю: %d (помечены в колонке причины)" % flagged)
        print("  %s" % client.usage_line())
    return {"written": written, "failed": failed, "flagged": flagged}
