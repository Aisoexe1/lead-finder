from __future__ import annotations

import json
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from .http import RateLimiter, make_session

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"


class GeminiError(RuntimeError):
    pass


class Gemini:
    """Тонкий клиент поверх REST API.

    Намеренно без SDK: у REST стабильный контракт, а зависимость меньше ломается
    при обновлениях. Модель задаётся в конфиге, её можно менять без правок кода.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.8-flash",
        temperature: float = 0.7,
        rpm: int = 12,
        max_retries: int = 6,
        verbose: bool = True,
        fallbacks: Optional[List[str]] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        # Популярные модели периодически отвечают 503 всем сразу. Чем стоять,
        # лучше доработать на запасной: качество отличается не настолько,
        # чтобы ради этого прерывать прогон.
        self.fallbacks = list(fallbacks or [])
        self.switched_from = ""
        self.temperature = temperature
        self.session = make_session()
        self.limiter = RateLimiter(rpm)
        self.max_retries = max_retries
        self.verbose = verbose
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def log(self, message: str) -> None:
        if self.verbose:
            print("  [gemini] %s" % message)

    # ------------------------------------------------------------------ вызов

    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        max_output_tokens: int = 4096,
        search: bool = False,
    ) -> str:
        generation_config: Dict[str, Any] = {
            "temperature": self.temperature if temperature is None else temperature,
            "maxOutputTokens": max_output_tokens,
        }
        if schema is not None:
            generation_config["responseMimeType"] = "application/json"
            generation_config["responseSchema"] = schema

        payload: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        if search:
            # поиск в вебе на стороне Google. Вместе с ним структурированный
            # вывод не работает, поэтому JSON просим словами и разбираем сами
            payload["tools"] = [{"google_search": {}}]
            generation_config.pop("responseMimeType", None)
            generation_config.pop("responseSchema", None)

        url = BASE_URL % self.model
        last_error = ""

        for attempt in range(self.max_retries):
            self.limiter.wait()
            try:
                resp = self.session.post(
                    url,
                    params={"key": self.api_key},
                    json=payload,
                    timeout=120,
                )
            except Exception as exc:
                last_error = str(exc)
                self._sleep(attempt)
                continue

            self.calls += 1

            if resp.status_code == 200:
                return self._extract_text(resp.json())

            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = "HTTP %d" % resp.status_code
                wait = self._retry_delay(resp, attempt)
                self.log("%s, жду %.0f c (попытка %d/%d)"
                         % (last_error, wait, attempt + 1, self.max_retries))
                time.sleep(wait)
                continue

            # 400/401/403 повторять смысла нет
            detail = resp.text[:400]
            raise GeminiError("Gemini вернул HTTP %d: %s" % (resp.status_code, detail))

        # основная модель не отвечает: пробуем запасную
        if self.fallbacks and ("503" in last_error or "429" in last_error):
            spare = self.fallbacks.pop(0)
            self.log("модель %s не отвечает (%s), перехожу на %s"
                     % (self.model, last_error, spare))
            if not self.switched_from:
                self.switched_from = self.model
            self.model = spare
            return self.generate(
                prompt, system=system, schema=schema, temperature=temperature,
                max_output_tokens=max_output_tokens, search=search,
            )

        raise GeminiError("Gemini не ответил после %d попыток (%s)"
                          % (self.max_retries, last_error))

    def generate_json(
        self,
        prompt: str,
        schema: Dict[str, Any],
        *,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_output_tokens: int = 4096,
        search: bool = False,
    ) -> Any:
        text = self.generate(
            prompt,
            system=system,
            schema=None if search else schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            search=search,
        )
        return _parse_json(text)

    def search_available(self) -> Tuple[bool, str]:
        """Доступен ли поиск в вебе. Возвращает (можно, причина отказа).

        Отличаем нехватку квоты от того, что модель поиск не умеет: в первом
        случае надо просто подождать, во втором сменить модель.
        """
        try:
            self.generate("Ответь одним словом: да", search=True, max_output_tokens=2048)
            return (True, "")
        except GeminiError as exc:
            text = str(exc)
            if "429" in text:
                return (False, "limit")
            if "400" in text or "404" in text:
                return (False, "unsupported")
            return (False, text[:160])

    # ------------------------------------------------------------ внутреннее

    def _extract_text(self, body: Dict[str, Any]) -> str:
        usage = body.get("usageMetadata") or {}
        self.tokens_in += usage.get("promptTokenCount", 0) or 0
        self.tokens_out += usage.get("candidatesTokenCount", 0) or 0

        candidates = body.get("candidates") or []
        if not candidates:
            feedback = body.get("promptFeedback") or {}
            raise GeminiError("пустой ответ, promptFeedback=%s" % json.dumps(feedback)[:200])

        candidate = candidates[0]
        finish = candidate.get("finishReason", "")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)

        if not text:
            raise GeminiError("ответ без текста, finishReason=%s" % finish)
        if finish == "MAX_TOKENS":
            self.log("ответ обрезан по лимиту токенов, уменьши batch_size")
        return text

    def _retry_delay(self, resp, attempt: int = 0) -> float:
        """Пауза перед повтором.

        503 означает, что модель перегружена у всех сразу, и короткий повтор
        почти наверняка упрётся в то же самое: таким ждём дольше.
        """
        header = resp.headers.get("Retry-After")
        if header:
            try:
                return min(120.0, float(header))
            except ValueError:
                pass
        base = 8.0 if resp.status_code == 503 else 4.0
        return min(90.0, base * (2 ** min(attempt, 4))) + random.uniform(0, 2.0)

    def _sleep(self, attempt: int) -> None:
        time.sleep(min(30.0, 2.0 ** attempt) + random.uniform(0, 0.8))

    def usage_line(self) -> str:
        line = "вызовов: %d, токенов вход/выход: %d/%d" % (
            self.calls, self.tokens_in, self.tokens_out
        )
        if self.switched_from:
            line += " (работала %s вместо %s)" % (self.model, self.switched_from)
        return line


def _parse_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except ValueError:
        # иногда модель добавляет текст вокруг JSON, вырезаем крайние скобки
        for opener, closer in (("[", "]"), ("{", "}")):
            start, end = text.find(opener), text.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except ValueError:
                    continue
        raise GeminiError("не удалось разобрать JSON из ответа: %s" % text[:300])


DEFAULT_FALLBACKS = ["gemini-3.5-flash", "gemini-3.1-flash-lite"]


def build_client(config, verbose: bool = True) -> Gemini:
    model = config.get("gemini.model", "gemini-3.8-flash")
    fallbacks = config.get("gemini.fallbacks")
    if fallbacks is None:
        fallbacks = DEFAULT_FALLBACKS
    return Gemini(
        api_key=config.gemini_key(),
        model=model,
        fallbacks=[m for m in fallbacks if m and m != model],
        temperature=float(config.get("gemini.temperature", 0.7)),
        rpm=int(config.get("gemini.rpm", 12)),
        max_retries=int(config.get("gemini.max_retries", 4)),
        verbose=verbose,
    )
