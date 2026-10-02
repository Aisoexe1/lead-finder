from __future__ import annotations

import json
import random
import time
from typing import Any, Dict, List, Optional

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
        model: str = "gemini-2.5-flash",
        temperature: float = 0.7,
        rpm: int = 12,
        max_retries: int = 4,
        verbose: bool = True,
    ) -> None:
        self.api_key = api_key
        self.model = model
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
                wait = self._retry_delay(resp)
                self.log("%s, жду %.0f c (попытка %d/%d)"
                         % (last_error, wait, attempt + 1, self.max_retries))
                time.sleep(wait)
                continue

            # 400/401/403 повторять смысла нет
            detail = resp.text[:400]
            raise GeminiError("Gemini вернул HTTP %d: %s" % (resp.status_code, detail))

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

    def search_available(self) -> bool:
        """Поддерживает ли выбранная модель поиск в вебе.

        Проверяем одним дешёвым запросом: аккаунты и модели различаются,
        и падать посреди работы из-за этого не хочется.
        """
        try:
            self.generate("Ответь одним словом: да", search=True, max_output_tokens=2048)
            return True
        except GeminiError as exc:
            self.log("поиск в вебе недоступен (%s)" % str(exc)[:120])
            return False

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

    def _retry_delay(self, resp) -> float:
        header = resp.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        return min(60.0, 4.0 * (2 ** min(self.calls % 4, 3))) + random.uniform(0, 1.5)

    def _sleep(self, attempt: int) -> None:
        time.sleep(min(30.0, 2.0 ** attempt) + random.uniform(0, 0.8))

    def usage_line(self) -> str:
        return "вызовов: %d, токенов вход/выход: %d/%d" % (
            self.calls, self.tokens_in, self.tokens_out
        )


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


def build_client(config, verbose: bool = True) -> Gemini:
    return Gemini(
        api_key=config.gemini_key(),
        model=config.get("gemini.model", "gemini-2.5-flash"),
        temperature=float(config.get("gemini.temperature", 0.7)),
        rpm=int(config.get("gemini.rpm", 12)),
        max_retries=int(config.get("gemini.max_retries", 4)),
        verbose=verbose,
    )
