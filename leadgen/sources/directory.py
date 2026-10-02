from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import get, make_session
from ..models import Lead
from .base import Source, social_url

PHONE_RE = re.compile(r"(?:\+?\d[\d\-\s()]{7,}\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


class DirectorySource(Source):
    """Универсальный парсер каталогов фирм по CSS-селекторам из конфига.

    Селекторы конкретных сайтов живут в config.yaml, а не в коде: вёрстка
    каталогов меняется часто, и править YAML быстрее, чем python-файл.
    """

    name = "directory"

    def __init__(self, settings: Dict[str, Any], verbose: bool = True) -> None:
        super().__init__(settings, verbose)
        self.session = make_session()
        self.delay = float(settings.get("delay", 1.5))

    def search(self, niche: str, city: str, limit: int) -> Iterator[Lead]:
        sites: List[Dict[str, Any]] = self.settings.get("sites") or []
        if not sites:
            self.log("в конфиге нет ни одного каталога (sources.directory.sites)")
            return

        for site in sites:
            yielded = 0
            site_name = site.get("name", "directory")
            template = site.get("search_url", "")
            if not template:
                self.log("%s: не задан search_url" % site_name)
                continue

            for page in range(1, int(site.get("max_pages", 2)) + 1):
                if yielded >= limit:
                    break
                url = (
                    template.replace("{niche}", _q(niche))
                    .replace("{city}", _q(city))
                    .replace("{page}", str(page))
                )
                resp = get(self.session, url, timeout=25)
                if resp is None or resp.status_code != 200:
                    self.log("%s: страница %d недоступна" % (site_name, page))
                    break

                if site.get("encoding"):
                    resp.encoding = site["encoding"]
                soup = BeautifulSoup(resp.text, "html.parser")
                cards = soup.select(site.get("item", "")) if site.get("item") else []
                if not cards:
                    self.log("%s: на странице %d карточек не найдено" % (site_name, page))
                    break

                for card in cards:
                    if yielded >= limit:
                        break
                    lead = self._card_to_lead(card, site, city, resp.url)
                    if lead is None:
                        continue
                    yield lead
                    yielded += 1

                time.sleep(self.delay)

            self.log("%s: собрано %d" % (site_name, yielded))

    def _card_to_lead(
        self, card, site: Dict[str, Any], city: str, base_url: str
    ) -> Optional[Lead]:
        fields: Dict[str, Any] = site.get("fields") or {}
        values = {
            key: _extract(card, rule, base_url) for key, rule in fields.items()
        }

        name = values.get("name", "")
        if not name:
            return None

        text = card.get_text(" ", strip=True)
        phone = values.get("phone") or _first(PHONE_RE, text)
        email = values.get("email") or _first(EMAIL_RE, text)
        website = values.get("website", "")

        # у кого уже есть сайт — не наш клиент
        if website and not any(
            s in website.lower() for s in ("instagram.com", "facebook.com", "t.me")
        ):
            return None
        if not (phone or email):
            return None

        return Lead(
            source="%s:%s" % (self.name, site.get("name", "?")),
            external_id=values.get("external_id", "") or name[:60],
            name=name,
            category=values.get("category", ""),
            city=city,
            address=values.get("address", ""),
            phone=phone or "",
            email=email or "",
            instagram=social_url(website, "instagram")
                      if "instagram.com" in (website or "").lower() else "",
            facebook=social_url(website, "facebook")
                     if "facebook.com" in (website or "").lower() else "",
            raw={"directory": site.get("name"), "url": values.get("url", "")},
        )


def _extract(card, rule: Any, base_url: str) -> str:
    """Правило поля: строка-селектор или {selector, attr}."""
    if isinstance(rule, str):
        selector, attr = rule, None
    elif isinstance(rule, dict):
        selector, attr = rule.get("selector", ""), rule.get("attr")
    else:
        return ""

    node = card.select_one(selector) if selector else None
    if node is None:
        return ""
    if attr:
        value = (node.get(attr) or "").strip()
        if attr == "href" and value:
            return urljoin(base_url, value)
        return value
    return node.get_text(" ", strip=True)


def _first(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text or "")
    return match.group(0).strip() if match else ""


def _q(value: str) -> str:
    from urllib.parse import quote_plus

    return quote_plus(value or "")
