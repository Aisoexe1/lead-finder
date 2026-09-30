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
