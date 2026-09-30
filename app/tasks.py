"""Фоновые задачи приложения.

Tkinter однопоточный: если дёрнуть Overpass или Gemini прямо в обработчике
кнопки, окно замрёт на минуты. Поэтому работа уходит в отдельный поток,
а обратно в интерфейс попадает только через очередь.
"""
from __future__ import annotations

import contextlib
import queue
import threading
import traceback
from typing import Any, Callable, Dict, Optional

LOG = "log"
DONE = "done"
FAIL = "fail"


class Reporter:
    """То, что видит фоновая задача: канал для строк лога."""

    def __init__(self, q: "queue.Queue[tuple]") -> None:
        self.q = q

    def log(self, message: str = "") -> None:
        self.q.put((LOG, str(message).rstrip()))

    # позволяет подменить stdout внутри шагов конвейера
    def write(self, text: str) -> int:
        for line in text.splitlines():
            if line.strip():
                self.log(line)
        return len(text)

    def flush(self) -> None:
        pass

    @contextlib.contextmanager
    def capture_stdout(self):
        with contextlib.redirect_stdout(self):
            yield


class TaskRunner:
    """Одна задача за раз.

    Параллелить нельзя не из-за кода, а из-за чужих лимитов: Overpass и Gemini
    быстро отвечают 429, если стучаться в несколько потоков.
    """

    def __init__(self) -> None:
        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self.current: str = ""

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, name: str, target: Callable[[Reporter], Any]) -> bool:
        if self.busy:
            return False
        self.current = name
        reporter = Reporter(self.queue)

        def wrapper() -> None:
            try:
                result = target(reporter)
                self.queue.put((DONE, result if isinstance(result, dict) else {}))
            except Exception as exc:
                self.queue.put((LOG, "ошибка: %s" % exc))
                tb = traceback.format_exc().strip().splitlines()
                if tb:
                    self.queue.put((LOG, tb[-1]))
                self.queue.put((FAIL, str(exc)))

        self._thread = threading.Thread(target=wrapper, daemon=True)
        self._thread.start()
        return True

    def drain(self):
        """Забирает накопившиеся сообщения. Вызывается из главного потока."""
        items = []
        while True:
            try:
                items.append(self.queue.get_nowait())
            except queue.Empty:
                break
        return items
