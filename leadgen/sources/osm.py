from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterator, List, Optional

import hashlib
import json
import os

from ..http import get, make_session
from ..models import Lead
from .base import Source, has_website, pick, social_from_links, social_url

NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Зеркала Overpass. Публичные инстансы регулярно отвечают 504 под нагрузкой,
# поэтому их несколько и запрос повторяется кругами с нарастающей паузой.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
OVERPASS_ROUNDS = 3

# Ниша -> OSM-теги. Токен с "$" на конце матчится как целое слово
# (чтобы "сто" не ловилось внутри "стоматология"), остальные — как начало слова
# (чтобы "красот" покрывал "красоты", "авто" — "автосервис").
NICHE_TAGS: Dict[str, List[str]] = {
    "красот|beauty|салон|барбер|barber|парикмах|перукар|нігт|ногт|nail|брів|бров|epil|епіл|депіл|депил":
        ['shop=beauty', 'shop=hairdresser', 'shop=nails', 'shop=massage', 'leisure=spa'],
    "стомат|dental|зуб|ортодонт":
        ['amenity=dentist', 'healthcare=dentist'],
    "кафе|cafe|кав\'|кофе|coffee|кофейн|кав":
        ['amenity=cafe'],
    "ресторан|restaurant|їдальн|столов|піцер|пиццер|суші|суши":
        ['amenity=restaurant', 'amenity=fast_food'],
    "бар$|bar$|паб$|pub$|пивн":
        ['amenity=bar', 'amenity=pub'],
    "пекарн|bakery|кондитер|торт|випічк|выпечк":
        ['shop=bakery', 'shop=confectionery', 'shop=pastry'],
    "фитнес|фітнес|gym$|спорт|тренаж|йог|кросфіт|кроссфит":
        ['leisure=fitness_centre', 'leisure=sports_centre'],
    "авто|сто$|шином|tyre|tire|car_repair|автосервіс|автосервис":
        ['shop=car_repair', 'shop=tyres', 'shop=car_parts', 'shop=car'],
    "мойк|мийк|car_wash|автомий|автомой":
        ['amenity=car_wash'],
    "ветер|vet$|тварин|животн|зоомаг":
        ['amenity=veterinary', 'shop=pet'],
    "юрист|адвокат|lawyer|нотар|юридич":
        ['office=lawyer', 'office=notary'],
    "бухгалт|accountant|аудит|податков|налогов":
        ['office=accountant', 'office=tax_advisor'],
    "клінінг|клининг|cleaning|прибиральн|химчист|хімчист|пральн|laundry":
        ['shop=laundry', 'shop=dry_cleaning'],
    "мебл|мебел|furniture|кухн":
        ['shop=furniture', 'shop=kitchen', 'craft=carpenter'],
    "будів|строит|ремонт кварт|construct|оздобл|отделоч|сантехн|електрик|электрик":
        ['craft=builder', 'craft=plumber', 'craft=electrician', 'craft=painter',
         'craft=carpenter', 'shop=doityourself', 'shop=hardware'],
    "квіт|цвет|flower|florist|флорист":
        ['shop=florist'],
    "фото|photo|фотограф":
        ['shop=photo', 'craft=photographer'],
    "турист|travel|турагент|турфірм|турфирм":
        ['shop=travel_agency'],
    "школ|курс|навч|обуч|репетит|language|автошкол":
        ['amenity=language_school', 'amenity=driving_school', 'office=educational_institution'],
    "медиц|медич|клінік|клиник|clinic|лікар|врач|діагност|диагност":
        ['amenity=clinic', 'amenity=doctors', 'healthcare=centre'],
    "аптек|pharmacy":
        ['amenity=pharmacy'],
    "готел|отел|hotel|хостел|hostel|апартамент":
        ['tourism=hotel', 'tourism=guest_house', 'tourism=hostel'],
    "нерух|недвиж|estate|ріелт|риелт":
        ['office=estate_agent'],
    "одяг|одежд|cloth|взутт|обувь|shoes":
        ['shop=clothes', 'shop=shoes'],
}

# если ниша не распозналась, берём широкий срез малого бизнеса
FALLBACK_TAGS = [
    'shop', 'craft', 'office', 'amenity=cafe', 'amenity=restaurant',
    'amenity=clinic', 'amenity=dentist', 'leisure=fitness_centre',
]


def _token_regex(token: str) -> str:
    """Токен с "$" — целое слово, иначе достаточно совпадения в начале слова."""
    if token.endswith("$"):
        return r"\b%s\b" % re.escape(token[:-1])
    return r"\b%s" % re.escape(token)


def tags_for_niche(niche: str, override: Optional[List[str]] = None) -> List[str]:
    if override:
        return list(override)
    lowered = (niche or "").lower()
    matched: List[str] = []
    for pattern, tags in NICHE_TAGS.items():
        for token in pattern.split("|"):
            if token and re.search(_token_regex(token), lowered, re.UNICODE):
                matched.extend(tags)
                break
    if matched:
        seen = set()
        return [t for t in matched if not (t in seen or seen.add(t))]
    return list(FALLBACK_TAGS)


class OSMSource(Source):
    """OpenStreetMap через Overpass API.

    Открытые данные (ODbL), ключ не нужен. Главный плюс для нашей задачи:
    в карточке POI есть тег website, и его отсутствие — прямой сигнал.
    """

    name = "osm"

    def __init__(self, settings: Dict[str, Any], verbose: bool = True) -> None:
        super().__init__(settings, verbose)
        self.session = make_session()
        self.session.headers["User-Agent"] = (
            "lead-finder/0.1 (contact: local script; https://openstreetmap.org)"
        )

    # ------------------------------------------------------------- геокодинг

    def _area_id(self, city: str) -> Optional[int]:
        """Город -> Overpass area id. Nominatim просит не чаще 1 запроса в секунду."""
        time.sleep(1.1)
        resp = get(
            self.session,
            NOMINATIM,
            params={"q": city, "format": "json", "limit": 1, "featuretype": "settlement"},
            timeout=20,
        )
        if resp is None or resp.status_code != 200:
            self.log("Nominatim не ответил по городу %s" % city)
            return None
        try:
            results = resp.json()
        except ValueError:
            return None
        if not results:
            self.log("город %s не найден в Nominatim" % city)
            return None
        entry = results[0]
        osm_id = int(entry["osm_id"])
        osm_type = entry.get("osm_type", "relation")
        if osm_type == "relation":
            return 3600000000 + osm_id
        if osm_type == "way":
            return 2400000000 + osm_id
        self.log("город %s найден как node, площадь построить нельзя" % city)
        return None

    # ---------------------------------------------------------------- запрос

    def _build_query(self, area_id: int, tags: List[str], limit: int) -> str:
        parts = []
        for tag in tags:
            if "=" in tag:
                key, value = tag.split("=", 1)
                selector = '["%s"="%s"]' % (key, value)
            else:
                selector = '["%s"]' % tag
            parts.append("  nwr%s(area.a);" % selector)
        return (
            "[out:json][timeout:120];\n"
            "area(%d)->.a;\n"
            "(\n%s\n);\n"
            "out center tags %d;" % (area_id, "\n".join(parts), limit * 4)
        )

    # ------------------------------------------------------------------ кэш

    def _cache_path(self, query: str) -> Optional[str]:
        folder = self.settings.get("cache_dir") or os.path.join("data", "overpass")
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return None
        return os.path.join(folder, "%s.json" % digest)

    def _from_cache(self, query: str) -> Optional[List[Dict[str, Any]]]:
        """Ответы Overpass живут сутки: данные OSM за это время почти не меняются,
        а сервера часто перегружены, и повторный прогон иначе упирается в 504."""
        hours = float(self.settings.get("cache_hours", 24) or 0)
        if hours <= 0:
            return None
        path = self._cache_path(query)
        if not path or not os.path.exists(path):
            return None
        if (time.time() - os.path.getmtime(path)) > hours * 3600:
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, OSError):
            return None
        self.log("беру из кэша (%d объектов), запрос не повторяю" % len(data))
        return data

    def _to_cache(self, query: str, elements: List[Dict[str, Any]]) -> None:
        if float(self.settings.get("cache_hours", 24) or 0) <= 0 or not elements:
            return
        path = self._cache_path(query)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(elements, fh, ensure_ascii=False)
        except OSError:
            pass

    def _run_overpass(self, query: str) -> List[Dict[str, Any]]:
        """Обходит зеркала по кругу: перегруженный инстанс через минуту
        часто отвечает нормально, поэтому одного прохода мало."""
        cached = self._from_cache(query)
        if cached is not None:
            return cached

        for round_number in range(1, OVERPASS_ROUNDS + 1):
            for endpoint in OVERPASS_ENDPOINTS:
                host = endpoint.split("/")[2]
                try:
                    resp = self.session.post(endpoint, data={"data": query}, timeout=180)
                except Exception as exc:  # сеть, таймаут, что угодно
                    self.log("%s: %s" % (host, exc))
                    continue

                if resp.status_code == 200:
                    try:
                        elements = resp.json().get("elements", [])
                    except ValueError:
                        self.log("%s вернул не JSON" % host)
                        continue
                    self._to_cache(query, elements)
                    return elements

                if resp.status_code in (429, 504, 503, 502):
                    self.log("%s занят (HTTP %d)" % (host, resp.status_code))
                else:
                    self.log("%s вернул HTTP %d" % (host, resp.status_code))
                time.sleep(1.5)

            if round_number < OVERPASS_ROUNDS:
                pause = 15 * round_number
                self.log("все зеркала заняты, пауза %d с и ещё попытка (%d из %d)"
                         % (pause, round_number + 1, OVERPASS_ROUNDS))
                time.sleep(pause)

        self.log("Overpass недоступен: все зеркала отвечают отказом")
        self.log("это перегрузка на их стороне, попробуй через несколько минут")
        return []

    # ----------------------------------------------------------------- поиск

    def search(self, niche: str, city: str, limit: int) -> Iterator[Lead]:
        area_id = self._area_id(city)
        if area_id is None:
            return

        tags = tags_for_niche(niche, self.settings.get("tags"))
        self.log("теги: %s" % ", ".join(tags[:8]) + (" ..." if len(tags) > 8 else ""))

        elements = self._run_overpass(self._build_query(area_id, tags, limit))
        self.log("Overpass вернул %d объектов" % len(elements))

        yielded = 0
        skipped_site = 0
        skipped_contact = 0

        for element in elements:
            if yielded >= limit:
                break
            tags_dict: Dict[str, str] = element.get("tags") or {}
            name = pick(tags_dict, "name", "name:uk", "name:ru", "operator", "brand")
            if not name:
                continue
            if has_website(tags_dict):
                skipped_site += 1
                continue

            phone = pick(tags_dict, "phone", "contact:phone", "contact:mobile", "mobile")
            email = pick(tags_dict, "email", "contact:email")
            instagram = social_url(
                pick(tags_dict, "contact:instagram", "instagram"), "instagram")
            facebook = social_url(
                pick(tags_dict, "contact:facebook", "facebook"), "facebook")

            # ссылку на профиль нередко кладут в website или url: тогда это
            # не сайт, а как раз признак, что сайта нет
            if not (instagram and facebook):
                from_links = social_from_links(
                    pick(tags_dict, "website", "contact:website", "url", "contact:url"),
                    pick(tags_dict, "contact:vk", "vk"),
                )
                instagram = instagram or from_links["instagram"]
                facebook = facebook or from_links["facebook"]
            if not (phone or email or instagram or facebook):
                skipped_contact += 1
                continue

            center = element.get("center") or {}
            address_parts = [
                pick(tags_dict, "addr:street"),
                pick(tags_dict, "addr:housenumber"),
            ]
            address = " ".join(p for p in address_parts if p)

            category = ""
            for key in ("shop", "craft", "office", "amenity", "healthcare", "leisure", "tourism"):
                if tags_dict.get(key):
                    category = "%s=%s" % (key, tags_dict[key])
                    break

            yield Lead(
                source=self.name,
                external_id="%s/%s" % (element.get("type"), element.get("id")),
                name=name,
                category=category,
                city=city,
                address=address,
                phone=phone,
                email=email,
                instagram=instagram,
                facebook=facebook,
                lat=element.get("lat") or center.get("lat"),
                lon=element.get("lon") or center.get("lon"),
                raw={"osm_tags": tags_dict},
            )
            yielded += 1

        self.log(
            "подошло %d, отброшено: с сайтом %d, без контактов %d"
            % (yielded, skipped_site, skipped_contact)
        )
