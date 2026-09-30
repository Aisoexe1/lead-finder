from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterator, List

from ..http import make_session, post_json
from ..models import Lead
from .base import Source

SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.primaryTypeDisplayName",
    "places.primaryType",
    "places.rating",
    "places.userRatingCount",
    "places.location",
    "places.businessStatus",
    "nextPageToken",
])


class GooglePlacesSource(Source):
    """Google Places API (New), Text Search.

    Официальный платный API, не скрапинг выдачи. Нужен ключ в GOOGLE_MAPS_API_KEY
    и включённый Places API (New) в облачном проекте.
    """

    name = "google_places"

    def __init__(self, settings: Dict[str, Any], verbose: bool = True) -> None:
        super().__init__(settings, verbose)
        self.session = make_session()
        self.api_key = os.environ.get(
            settings.get("api_key_env", "GOOGLE_MAPS_API_KEY"), ""
        ).strip()

    def search(self, niche: str, city: str, limit: int) -> Iterator[Lead]:
        if not self.api_key:
            self.log("нет ключа GOOGLE_MAPS_API_KEY, источник пропущен")
            return

        query = "%s %s" % (niche, city)
        max_pages = int(self.settings.get("max_pages", 3))
        page_token = None
        yielded = 0
        skipped_site = 0
        skipped_closed = 0

        for page in range(max_pages):
            if yielded >= limit:
                break
            payload: Dict[str, Any] = {
                "textQuery": query,
                "languageCode": self.settings.get("language", "uk"),
                "pageSize": 20,
            }
            if page_token:
                payload["pageToken"] = page_token

            resp = post_json(
                self.session,
                SEARCH_TEXT_URL,
                payload,
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": FIELD_MASK,
                    "Content-Type": "application/json",
                },
                timeout=40,
            )
            if resp is None:
                self.log("Places API не ответил")
                return
            if resp.status_code != 200:
                self.log("Places API вернул HTTP %d: %s" % (resp.status_code, resp.text[:200]))
                return

            body = resp.json()
            places: List[Dict[str, Any]] = body.get("places") or []
            if not places:
                break

            for place in places:
                if yielded >= limit:
                    break
                if place.get("businessStatus") in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
                    skipped_closed += 1
                    continue

                website = (place.get("websiteUri") or "").strip()
                if website and not _is_social_only(website):
                    skipped_site += 1
                    continue

                name = (place.get("displayName") or {}).get("text", "")
                if not name:
                    continue
                phone = (
                    place.get("internationalPhoneNumber")
                    or place.get("nationalPhoneNumber")
                    or ""
                )
                if not phone:
                    continue

                location = place.get("location") or {}
                category = (place.get("primaryTypeDisplayName") or {}).get(
                    "text", place.get("primaryType", "")
                )

                lead = Lead(
                    source=self.name,
                    external_id=place.get("id", ""),
                    name=name,
                    category=category,
                    city=city,
                    address=place.get("formattedAddress", ""),
                    phone=phone,
                    lat=location.get("latitude"),
                    lon=location.get("longitude"),
                    rating=place.get("rating"),
                    reviews=place.get("userRatingCount"),
                    raw={"google_place_id": place.get("id")},
                )
                # соцсеть вместо сайта — это как раз наш клиент
                if website:
                    lead.raw["social_as_website"] = website
                    if "instagram.com" in website:
                        lead.instagram = website
                    elif "facebook.com" in website:
                        lead.facebook = website
                yield lead
                yielded += 1

            page_token = body.get("nextPageToken")
            if not page_token:
                break
            time.sleep(2)  # токен следующей страницы активируется не мгновенно

        self.log(
            "подошло %d, отброшено: с сайтом %d, закрытых %d"
            % (yielded, skipped_site, skipped_closed)
        )


def _is_social_only(url: str) -> bool:
    lowered = url.lower()
    return any(
        domain in lowered
        for domain in ("instagram.com", "facebook.com", "linktr.ee", "taplink",
                       "t.me", "vk.com", "tiktok.com")
    )
