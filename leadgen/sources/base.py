from __future__ import annotations

from typing import Any, Dict, Iterator, List

from ..models import Lead


class Source:
    """Общий интерфейс источника лидов.

    Источник обязан возвращать сырые лиды. Решение «мусор или нет» принимается
    позже, на этапах verify и filter, чтобы источники оставались простыми.
    """

    name = "base"

    def __init__(self, settings: Dict[str, Any], verbose: bool = True) -> None:
        self.settings = settings or {}
        self.verbose = verbose

    def search(self, niche: str, city: str, limit: int) -> Iterator[Lead]:
        raise NotImplementedError

    def log(self, message: str) -> None:
        if self.verbose:
            print("  [%s] %s" % (self.name, message))


def has_website(tags: Dict[str, str]) -> bool:
    """В OSM и каталогах сайт лежит в разных ключах, проверяем все."""
    for key in ("website", "contact:website", "url", "contact:url", "website:official"):
        value = (tags.get(key) or "").strip()
        if value and not value.lower().startswith(("facebook.", "instagram.", "http://facebook",
                                                   "https://facebook", "https://instagram",
                                                   "http://instagram")):
            return True
    return False


def pick(tags: Dict[str, str], *keys: str) -> str:
    for key in keys:
        value = (tags.get(key) or "").strip()
        if value:
            return value
    return ""


SOCIAL_HOSTS = {
    "instagram": ("instagram.com", "instagr.am"),
    "facebook": ("facebook.com", "fb.com", "fb.me", "m.facebook.com"),
}


def social_url(value: str, network: str) -> str:
    """Приводит запись соцсети к полной ссылке.

    В OSM в это поле пишут что угодно: полный адрес, ник с собачкой, просто
    ник. Без нормализации такие записи нельзя ни открыть, ни сравнить.
    """
    value = (value or "").strip()
    if not value:
        return ""

    hosts = SOCIAL_HOSTS.get(network, ())
    lowered = value.lower()

    if lowered.startswith(("http://", "https://")) or any(h in lowered for h in hosts):
        url = value if lowered.startswith(("http://", "https://")) else "https://" + value
        return url.split("?")[0].rstrip("/")

    handle = value.lstrip("@").strip("/")
    if not handle or "/" in handle or " " in handle:
        return ""
    domain = hosts[0] if hosts else "instagram.com"
    return "https://%s/%s" % (domain, handle)


def social_from_links(*values: str) -> Dict[str, str]:
    """Вылавливает соцсети среди ссылок: в OSM их часто кладут в website."""
    found = {"instagram": "", "facebook": ""}
    for value in values:
        lowered = (value or "").lower()
        for network, hosts in SOCIAL_HOSTS.items():
            if not found[network] and any(host in lowered for host in hosts):
                found[network] = social_url(value, network)
    return found


def registry() -> Dict[str, type]:
    from .osm import OSMSource
    from .google_places import GooglePlacesSource
    from .directory import DirectorySource
    from .social import SocialSource

    return {
        "osm": OSMSource,
        "google_places": GooglePlacesSource,
        "directory": DirectorySource,
        "social": SocialSource,
    }
