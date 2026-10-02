from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

# статусы, которые лид проходит по конвейеру
NEW = "new"
VERIFIED = "verified"
KEPT = "kept"
DROPPED = "dropped"
WRITTEN = "written"


def _norm(value: Optional[str]) -> str:
    """Грубая нормализация для дедупликации: без регистра, пунктуации и лишних пробелов."""
    if not value:
        return ""
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


@dataclass
class Lead:
    # --- собрано скрапером ---
    source: str = ""
    external_id: str = ""
    name: str = ""
    category: str = ""
    city: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    instagram: str = ""
    facebook: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    rating: Optional[float] = None
    reviews: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    # --- этап verify ---
    phone_e164: str = ""
    phone_kind: str = ""  # mobile | landline | tollfree | unknown
    website_status: str = ""  # absent | dead | alive | found_in_search

    # --- этап filter (Gemini) ---
    ai_score: Optional[int] = None
    ai_verdict: str = ""  # keep | drop
    ai_reason: str = ""

    # --- этап write (Gemini) ---
    message_1: str = ""
    message_2: str = ""

    status: str = NEW
    sent_at: str = ""

    # --- что было после отправки ---
    replied_at: str = ""
    outcome: str = ""     # replied | refused | thinking | client
    note: str = ""        # заметка от руки
    next_touch: str = ""  # дата следующего касания, YYYY-MM-DD

    # Идентификатор считается один раз и дальше живёт вместе с записью.
    # Вычислять его на лету нельзя: поля вроде phone_e164 появляются позже,
    # ключ уехал бы и обновление ушло мимо строки в базе.
    id_: str = ""

    @property
    def id(self) -> str:
        if not self.id_:
            self.id_ = self._compute_id()
        return self.id_

    def _compute_id(self) -> str:
        """Ключ строится только из того, что известно уже на этапе скрапинга."""
        if self.external_id:
            key = "%s:%s" % (self.source, self.external_id)
        else:
            key = "%s|%s" % (_norm(self.name), _norm(self.city))
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    @property
    def dedup_key(self) -> str:
        """Второй ключ: имя + город. Ловит дубли, у которых телефоны записаны по-разному."""
        return hashlib.sha1(
            ("%s|%s" % (_norm(self.name), _norm(self.city))).encode("utf-8")
        ).hexdigest()[:16]

    @property
    def has_contact(self) -> bool:
        return bool(self.phone or self.email or self.instagram or self.facebook)

    @property
    def can_message(self) -> bool:
        """Можно ли написать в мессенджер. У городских номеров аккаунтов не бывает."""
        return self.phone_kind in ("mobile", "unknown") and bool(self.phone_e164)

    @property
    def reachable_without_phone(self) -> bool:
        return bool(self.email or self.instagram or self.facebook)

    def to_row(self) -> Dict[str, Any]:
        row = asdict(self)
        row["raw"] = json.dumps(self.raw, ensure_ascii=False)
        row["id"] = self.id
        row["dedup_key"] = self.dedup_key
        return row

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Lead":
        row = dict(row)
        data = {k: v for k, v in row.items() if k in cls.__dataclass_fields__}
        if row.get("id"):
            data["id_"] = row["id"]
        raw = data.get("raw")
        if isinstance(raw, str):
            try:
                data["raw"] = json.loads(raw)
            except (ValueError, TypeError):
                data["raw"] = {}
        for key in ("id_", "source", "external_id", "name", "category", "city", "address",
                    "phone", "email", "website", "instagram", "facebook",
                    "phone_e164", "phone_kind", "website_status", "ai_verdict", "ai_reason",
                    "message_1", "message_2", "status", "sent_at",
                    "replied_at", "outcome", "note", "next_touch"):
            if data.get(key) is None:
                data[key] = ""
        return cls(**data)

    def context_for_ai(self) -> Dict[str, Any]:
        """Компактный срез, который уходит в Gemini. Лишнее не шлём, чтобы не жечь токены."""
        out = {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "city": self.city,
            "address": self.address,
            "source": self.source,
        }
        if self.rating is not None:
            out["rating"] = self.rating
        if self.reviews is not None:
            out["reviews"] = self.reviews
        if self.website:
            out["website"] = self.website
        out["website_status"] = self.website_status or "absent"
        out["has_phone"] = bool(self.phone_e164 or self.phone)
        if self.phone_kind:
            out["phone_kind"] = self.phone_kind
        out["has_email"] = bool(self.email)
        out["has_instagram"] = bool(self.instagram)
        return out


# исходы после отправки: ключ, подпись, считается ли работа законченной
OUTCOMES = [
    ("replied", "ответил", False),
    ("thinking", "думает", False),
    ("client", "клиент", True),
    ("refused", "отказ", True),
]

CLOSED_OUTCOMES = {key for key, _, closed in OUTCOMES if closed}
