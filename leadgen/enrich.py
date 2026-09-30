from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

import requests

from .http import make_session
from .models import Lead, VERIFIED
from .storage import Store

try:
    import phonenumbers

    HAVE_PHONENUMBERS = True
except ImportError:  # работаем и без библиотеки, просто грубее
    HAVE_PHONENUMBERS = False

DIGITS_RE = re.compile(r"\d+")


def normalize_phone(raw: str, region: str = "UA") -> str:
    """Телефон в формат E.164. Нужен для ссылок wa.me и для дедупликации."""
    if not raw:
        return ""
    # в карточках часто несколько номеров через запятую, берём первый
    raw = re.split(r"[,;/]| или | або ", raw)[0].strip()

    if HAVE_PHONENUMBERS:
        try:
            parsed = phonenumbers.parse(raw, region)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
        except Exception:
            pass
        return ""

    digits = "".join(DIGITS_RE.findall(raw))
    if not digits:
        return ""
    if raw.strip().startswith("+"):
        return "+" + digits
    if region == "UA":
        if len(digits) == 10 and digits.startswith("0"):
            return "+38" + digits
        if len(digits) == 12 and digits.startswith("380"):
            return "+" + digits
    return ""


def check_website(url: str, timeout: int = 8) -> str:
    """alive | dead. Мёртвый сайт — тоже повод написать, поэтому лид не выбрасываем."""
    if not url:
        return "absent"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    session = make_session()
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        if resp.status_code < 400 and len(resp.content) > 512:
            return "alive"
        return "dead"
    except requests.RequestException:
        return "dead"
    finally:
        session.close()


def verify_lead(lead: Lead, region: str, check_site: bool, timeout: int) -> Lead:
    lead.phone_e164 = normalize_phone(lead.phone, region)
    if lead.website and check_site:
        lead.website_status = check_website(lead.website, timeout)
    elif not lead.website and not lead.website_status:
        lead.website_status = "absent"
    lead.status = VERIFIED
    return lead


def run_verify(store: Store, config, verbose: bool = True) -> Tuple[int, int]:
    """Прогоняет новые лиды через нормализацию и проверку сайта.

    Возвращает (проверено, отброшено без контактов).
    """
    region = config.get("verify.region", "UA")
    check_site = bool(config.get("verify.check_website", True))
    timeout = int(config.get("verify.timeout", 8))
    workers = int(config.get("verify.workers", 8))

    leads = store.fetch(status="new")
    if not leads:
        return (0, 0)

    def work(lead: Lead) -> Lead:
        return verify_lead(lead, region, check_site, timeout)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        processed = list(pool.map(work, leads))

    kept, dropped = 0, 0
    for lead in processed:
        # без телефона, почты и соцсети писать некуда
        if not (lead.phone_e164 or lead.email or lead.instagram or lead.facebook):
            store.update_fields(lead.id, status="dropped", ai_reason="нет контактов")
            dropped += 1
            continue
        # сайт живой и это не соцсеть — клиент уже с сайтом
        if lead.website_status == "alive":
            store.update_fields(
                lead.id, status="dropped", website_status="alive",
                ai_reason="сайт уже есть и работает",
            )
            dropped += 1
            continue
        store.save(lead)
        kept += 1

    merged = store.merge_duplicates_by_phone()
    if merged:
        kept -= merged

    if verbose:
        print("  проверено: %d, отброшено: %d, слито дублей по телефону: %d"
              % (kept, dropped, merged))
    return (kept, dropped)
