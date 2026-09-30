from __future__ import annotations

import random
import threading
import time
from typing import Any, Dict, Optional

import requests

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class RateLimiter:
    """Простой лимитер: не чаще N запросов в минуту, потокобезопасный."""

    def __init__(self, rpm: int) -> None:
        self.interval = 60.0 / rpm if rpm and rpm > 0 else 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            delta = time.time() - self._last
            if delta < self.interval:
                time.sleep(self.interval - delta)
            self._last = time.time()


def make_session(timeout: int = 20) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept-Language": "uk,ru;q=0.9,en;q=0.8",
    })
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def get(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 20,
    retries: int = 3,
    backoff: float = 1.5,
) -> Optional[requests.Response]:
    """GET с ретраями на 429/5xx. Возвращает None, если так и не получилось."""
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException("HTTP %d" % resp.status_code)
            return resp
        except requests.RequestException:
            if attempt == retries - 1:
                return None
            time.sleep(backoff ** attempt + random.uniform(0, 0.4))
    return None


def post_json(
    session: requests.Session,
    url: str,
    payload: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 60,
    retries: int = 4,
    backoff: float = 2.0,
) -> Optional[requests.Response]:
    last: Optional[requests.Response] = None
    for attempt in range(retries):
        try:
            resp = session.post(url, json=payload, headers=headers, timeout=timeout)
            last = resp
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.RequestException("HTTP %d" % resp.status_code)
            return resp
        except requests.RequestException:
            if attempt == retries - 1:
                return last
            time.sleep(backoff ** attempt + random.uniform(0, 0.6))
    return last
