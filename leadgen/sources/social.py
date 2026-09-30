from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterator, List, Set
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from ..http import get, make_session
from ..models import Lead
from .base import Source

DDG_HTML = "https://html.duckduckgo.com/html/"

PROFILE_RE = {
    "instagram": re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})/?"),
    "facebook": re.compile(r"facebook\.com/([A-Za-z0-9_.\-]{3,60})/?"),
}

# служебные пути, которые не являются профилями
STOP_HANDLES = {
    "p", "reel", "reels", "explore", "accounts", "directory", "stories", "tv",
    "about", "privacy", "legal", "developer", "pages", "groups", "watch",
    "marketplace", "events", "help", "login", "sharer", "profile.php",
}


class SocialSource(Source):
    """Бизнес-профили в Instagram/Facebook, найденные через публичный веб-поиск.

    Важное ограничение: сами Instagram и Facebook закрывают карточки от
    неавторизованных запросов, а их правила запрещают автоматический сбор.
    Поэтому здесь нет ни логина, ни обхода защиты: мы берём только то, что
    отдала поисковая выдача (ник, заголовок, сниппет). Наличие сайта у такого
    лида остаётся неизвестным, и проверить его придётся глазами.
    """

    name = "social"

    def __init__(self, settings: Dict[str, Any], verbose: bool = True) -> None:
        super().__init__(settings, verbose)
        self.session = make_session()
        self.delay = float(settings.get("delay", 2.5))

    def search(self, niche: str, city: str, limit: int) -> Iterator[Lead]:
        networks: List[str] = self.settings.get("networks") or ["instagram"]
        max_results = min(int(self.settings.get("max_results", 40)), limit)
        seen: Set[str] = set()
        total = 0

        for network in networks:
            domain = "instagram.com" if network == "instagram" else "facebook.com"
            queries = [
                'site:%s %s %s' % (domain, niche, city),
                'site:%s "%s" %s запис' % (domain, city, niche),
            ]
            for query in queries:
                if total >= max_results:
                    break
                for handle, title, snippet in self._ddg(query, network):
                    if total >= max_results:
                        break
                    if handle.lower() in seen:
                        continue
                    seen.add(handle.lower())

                    profile_url = "https://%s/%s" % (domain, handle)
                    lead = Lead(
                        source="%s:%s" % (self.name, network),
                        external_id="%s/%s" % (network, handle),
                        name=_clean_title(title) or handle,
                        category=niche,
                        city=city,
                        instagram=profile_url if network == "instagram" else "",
                        facebook=profile_url if network == "facebook" else "",
                        website_status="unknown",
                        raw={"handle": handle, "snippet": snippet, "query": query},
                    )
                    # телефон иногда лежит прямо в сниппете
                    phone = re.search(r"\+?\d[\d\-\s()]{8,}\d", snippet or "")
                    if phone:
                        lead.phone = phone.group(0).strip()
                    yield lead
                    total += 1
                time.sleep(self.delay)

        self.log("найдено профилей: %d (наличие сайта нужно проверить вручную)" % total)

    def _ddg(self, query: str, network: str):
        resp = get(
            self.session,
            DDG_HTML,
            params={"q": query, "kl": "ua-uk"},
            timeout=25,
            retries=2,
        )
        if resp is None or resp.status_code != 200:
            self.log("поиск не отработал (возможна капча у DuckDuckGo)")
            return

        soup = BeautifulSoup(resp.text, "html.parser")
        pattern = PROFILE_RE[network]

        for result in soup.select(".result, .web-result"):
            link = result.select_one("a.result__a")
            if link is None:
                continue
            href = _unwrap(link.get("href", ""))
            match = pattern.search(href)
            if not match:
                continue
            handle = match.group(1).strip("/.")
            if not handle or handle.lower() in STOP_HANDLES:
                continue
            snippet_node = result.select_one(".result__snippet")
            yield (
                handle,
                link.get_text(" ", strip=True),
                snippet_node.get_text(" ", strip=True) if snippet_node else "",
            )


def _unwrap(href: str) -> str:
    """DuckDuckGo заворачивает ссылки в редирект /l/?uddg=..."""
    if "uddg=" in href:
        part = href.split("uddg=", 1)[1].split("&", 1)[0]
        return unquote(part)
    return href


def _clean_title(title: str) -> str:
    for marker in (" | Instagram", " • Instagram", " - Instagram", " | Facebook", " - Facebook"):
        if marker in title:
            title = title.split(marker, 1)[0]
    return title.strip(" -|•")
