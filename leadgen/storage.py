from __future__ import annotations

import os
import sqlite3
from typing import Dict, Iterable, List, Optional

from .models import Lead

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            TEXT PRIMARY KEY,
    dedup_key     TEXT,
    source        TEXT,
    external_id   TEXT,
    name          TEXT,
    category      TEXT,
    city          TEXT,
    address       TEXT,
    phone         TEXT,
    email         TEXT,
    website       TEXT,
    instagram     TEXT,
    facebook      TEXT,
    lat           REAL,
    lon           REAL,
    rating        REAL,
    reviews       INTEGER,
    raw           TEXT,
    phone_e164    TEXT,
    phone_kind    TEXT,
    website_status TEXT,
    ai_score      INTEGER,
    ai_verdict    TEXT,
    ai_reason     TEXT,
    message_1     TEXT,
    message_2     TEXT,
    status        TEXT,
    sent_at       TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_dedup ON leads(dedup_key);
CREATE INDEX IF NOT EXISTS idx_leads_city ON leads(city);
"""

FIELDS = [
    "id", "dedup_key", "source", "external_id", "name", "category", "city",
    "address", "phone", "email", "website", "instagram", "facebook", "lat",
    "lon", "rating", "reviews", "raw", "phone_e164", "phone_kind", "website_status",
    "ai_score", "ai_verdict", "ai_reason", "message_1", "message_2",
    "status", "sent_at", "replied_at", "outcome", "note", "next_touch",
]

# поля, которые при повторном скрапинге не затираем пустым значением из нового источника
_PRESERVE_IF_EMPTY = {
    "phone", "email", "website", "instagram", "facebook", "address",
    "category", "rating", "reviews", "lat", "lon",
}


class Store:
    def __init__(self, path: str = "data/leads.db") -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Базы, созданные прежними версиями, дополняем недостающими колонками."""
        have = {row["name"] for row in self.conn.execute("PRAGMA table_info(leads)")}
        for column, ddl in (
            ("phone_kind", "TEXT"),
            ("replied_at", "TEXT"),
            ("outcome", "TEXT"),
            ("note", "TEXT"),
            ("next_touch", "TEXT"),
        ):
            if column not in have:
                self.conn.execute("ALTER TABLE leads ADD COLUMN %s %s" % (column, ddl))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ запись

    def upsert(self, lead: Lead) -> str:
        """Добавляет лид или обогащает существующий. Возвращает 'new' | 'merged'."""
        row = lead.to_row()
        existing = self._find_existing(row["id"], row["dedup_key"], row.get("phone_e164", ""))

        if existing is None:
            columns = ", ".join(FIELDS)
            marks = ", ".join("?" for _ in FIELDS)
            self.conn.execute(
                "INSERT INTO leads (%s) VALUES (%s)" % (columns, marks),
                [row.get(f) for f in FIELDS],
            )
            self.conn.commit()
            return "new"

        # дубль: дописываем только то, чего раньше не было
        updates: Dict[str, object] = {}
        for field_name in _PRESERVE_IF_EMPTY:
            new_value = row.get(field_name)
            if new_value in (None, "", 0):
                continue
            if not existing[field_name]:
                updates[field_name] = new_value
        if row.get("source") and row["source"] not in (existing["source"] or ""):
            updates["source"] = "%s+%s" % (existing["source"], row["source"])

        if updates:
            assignments = ", ".join("%s = ?" % k for k in updates)
            self.conn.execute(
                "UPDATE leads SET %s, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
                % assignments,
                list(updates.values()) + [existing["id"]],
            )
            self.conn.commit()
        # лид продолжает жить под идентификатором той строки, в которую влился
        lead.id_ = existing["id"]
        return "merged"

    def _find_existing(
        self, lead_id: str, dedup_key: str, phone_e164: str = ""
    ) -> Optional[sqlite3.Row]:
        row = self.conn.execute(
            "SELECT * FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
        if row:
            return row
        row = self.conn.execute(
            "SELECT * FROM leads WHERE dedup_key = ? AND dedup_key != ''", (dedup_key,)
        ).fetchone()
        if row:
            return row
        if phone_e164:
            return self.conn.execute(
                "SELECT * FROM leads WHERE phone_e164 = ?", (phone_e164,)
            ).fetchone()
        return None

    def merge_duplicates_by_phone(self) -> int:
        """После нормализации телефонов всплывают дубли, которых не было видно
        по названию. Оставляем самую полную запись, остальные помечаем dropped."""
        groups = self.conn.execute(
            "SELECT phone_e164, COUNT(*) AS n FROM leads "
            "WHERE phone_e164 != '' AND status NOT IN ('dropped') "
            "GROUP BY phone_e164 HAVING n > 1"
        ).fetchall()

        merged = 0
        for group in groups:
            rows = self.conn.execute(
                "SELECT * FROM leads WHERE phone_e164 = ? AND status != 'dropped'",
                (group["phone_e164"],),
            ).fetchall()
            # самой полной считаем запись с наибольшим числом заполненных контактов
            def filled(r):
                return sum(
                    1 for f in ("email", "instagram", "facebook", "address", "category")
                    if r[f]
                )
            rows = sorted(rows, key=filled, reverse=True)
            keeper, rest = rows[0], rows[1:]
            for row in rest:
                updates = {
                    f: row[f] for f in ("email", "instagram", "facebook", "address", "category")
                    if row[f] and not keeper[f]
                }
                if updates:
                    assignments = ", ".join("%s = ?" % k for k in updates)
                    self.conn.execute(
                        "UPDATE leads SET %s WHERE id = ?" % assignments,
                        list(updates.values()) + [keeper["id"]],
                    )
                self.conn.execute(
                    "UPDATE leads SET status = 'dropped', ai_reason = ? WHERE id = ?",
                    ("дубль по телефону, слит с %s" % keeper["name"], row["id"]),
                )
                merged += 1
        self.conn.commit()
        return merged

    def update_fields(self, lead_id: str, **fields) -> None:
        if not fields:
            return
        assignments = ", ".join("%s = ?" % k for k in fields)
        self.conn.execute(
            "UPDATE leads SET %s, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
            % assignments,
            list(fields.values()) + [lead_id],
        )
        self.conn.commit()

    def save(self, lead: Lead) -> None:
        row = lead.to_row()
        self.update_fields(row["id"], **{f: row[f] for f in FIELDS if f != "id"})

    # ------------------------------------------------------------------ чтение

    def fetch(
        self,
        status: Optional[str] = None,
        statuses: Optional[Iterable[str]] = None,
        city: Optional[str] = None,
        limit: Optional[int] = None,
        only_with_contact: bool = False,
    ) -> List[Lead]:
        sql = "SELECT * FROM leads WHERE 1=1"
        params: List[object] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if statuses:
            values = list(statuses)
            sql += " AND status IN (%s)" % ", ".join("?" for _ in values)
            params.extend(values)
        if city:
            sql += " AND city = ?"
            params.append(city)
        if only_with_contact:
            sql += " AND (phone_e164 != '' OR phone != '' OR email != '' OR instagram != '')"
        sql += " ORDER BY COALESCE(ai_score, -1) DESC, name"
        if limit:
            sql += " LIMIT %d" % int(limit)
        return [Lead.from_row(r) for r in self.conn.execute(sql, params).fetchall()]

    def fetch_needing_verify(self) -> List[Lead]:
        """Новые лиды плюс те, что проверялись прежней версией и остались
        без типа номера. Иначе старая база не получила бы новых полей."""
        rows = self.conn.execute(
            "SELECT * FROM leads WHERE status = 'new' "
            "   OR (status = 'verified' AND (phone_kind IS NULL OR phone_kind = '') "
            "       AND phone_e164 != '') "
            "ORDER BY name"
        ).fetchall()
        return [Lead.from_row(r) for r in rows]

    def fetch_due(self, today: str) -> List[Lead]:
        """Кому писать сегодня: срок следующего касания наступил,
        а разговор ещё не закрыт отказом или сделкой."""
        rows = self.conn.execute(
            "SELECT * FROM leads "
            "WHERE next_touch != '' AND next_touch <= ? "
            "  AND COALESCE(outcome, '') NOT IN ('refused', 'client') "
            "  AND status != 'dropped' "
            "ORDER BY next_touch, name",
            (today,),
        ).fetchall()
        return [Lead.from_row(r) for r in rows]

    def funnel(self) -> Dict[str, int]:
        """Сводка по воронке: отправлено, ответили, чем кончилось."""
        c = self.conn
        out = {
            "sent": c.execute("SELECT COUNT(*) FROM leads WHERE sent_at != ''").fetchone()[0],
            "replied": c.execute(
                "SELECT COUNT(*) FROM leads WHERE COALESCE(outcome,'') != ''").fetchone()[0],
        }
        for key in ("replied", "thinking", "client", "refused"):
            out[key] = c.execute(
                "SELECT COUNT(*) FROM leads WHERE outcome = ?", (key,)).fetchone()[0]
        out["answered"] = out["thinking"] + out["client"] + out["refused"] + out["replied"]
        return out

    def get(self, lead_id: str) -> Optional[Lead]:
        row = self.conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return Lead.from_row(row) if row else None

    def counts(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM leads GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def stats(self) -> Dict[str, object]:
        c = self.conn
        total = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        by_source = {
            r["source"]: r["n"]
            for r in c.execute(
                "SELECT source, COUNT(*) AS n FROM leads GROUP BY source ORDER BY n DESC"
            ).fetchall()
        }
        by_city = {
            r["city"]: r["n"]
            for r in c.execute(
                "SELECT city, COUNT(*) AS n FROM leads GROUP BY city ORDER BY n DESC"
            ).fetchall()
        }
        with_phone = c.execute("SELECT COUNT(*) FROM leads WHERE phone_e164 != ''").fetchone()[0]
        return {
            "total": total,
            "by_status": self.counts(),
            "by_source": by_source,
            "by_city": by_city,
            "with_phone": with_phone,
        }
