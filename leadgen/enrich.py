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

# Городские номера Украины: 0 + код города (2-4 цифры). Запасной способ,
# когда библиотека phonenumbers не установлена.
UA_MOBILE_PREFIXES = {
    "039", "050", "063", "066", "067", "068", "073", "091", "092", "093",
    "094", "095", "096", "097", "098", "099",
}


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


def phone_kind(phone_e164: str) -> str:
    """mobile | landline | tollfree | unknown.

    Нужно затем, что у городского номера не бывает аккаунта в WhatsApp,
    Telegram или Viber: кнопка «написать» на нём бесполезна.
    """
    if not phone_e164:
        return ""

    if HAVE_PHONENUMBERS:
        try:
            parsed = phonenumbers.parse(phone_e164, None)
            kind = phonenumbers.number_type(parsed)
            if kind == phonenumbers.PhoneNumberType.MOBILE:
                return "mobile"
            if kind == phonenumbers.PhoneNumberType.FIXED_LINE:
                return "landline"
            if kind == phonenumbers.PhoneNumberType.TOLL_FREE:
                return "tollfree"
            if kind == phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE:
                return "unknown"
            return "unknown"
        except Exception:
            return "unknown"

    # без библиотеки умеем только украинские номера
    if phone_e164.startswith("+380") and len(phone_e164) == 13:
        prefix = "0" + phone_e164[4:6]
        if phone_e164[4:7] == "800":
            return "tollfree"
        return "mobile" if prefix in {p[:3] for p in UA_MOBILE_PREFIXES} else "landline"
    return "unknown"


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
    lead.phone_kind = phone_kind(lead.phone_e164)
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

    leads = store.fetch_needing_verify()
    if not leads:
        return (0, 0)

    def work(lead: Lead) -> Lead:
        return verify_lead(lead, region, check_site, timeout)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        processed = list(pool.map(work, leads))

    drop_landlines = bool(config.get("verify.drop_landlines", True))

    kept, dropped = 0, 0
    landlines = 0
    for lead in processed:
        # без телефона, почты и соцсети писать некуда
        if not (lead.phone_e164 or lead.email or lead.instagram or lead.facebook):
            store.update_fields(lead.id, status="dropped", ai_reason="нет контактов")
            dropped += 1
            continue

        # городской номер означает, что мессенджеры отпадают. Если других
        # контактов нет, писать такому лиду нечем, и он уходит в отсев.
        if drop_landlines and lead.phone_kind in ("landline", "tollfree"):
            if not lead.reachable_without_phone:
                store.update_fields(
                    lead.id, status="dropped", phone_kind=lead.phone_kind,
                    phone_e164=lead.phone_e164,
                    ai_reason="городской номер, мессенджеры недоступны",
                )
                landlines += 1
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
        if landlines:
            print("  из них городских номеров без других контактов: %d" % landlines)
    return (kept, dropped)
