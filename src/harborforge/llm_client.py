"""Multi-provider LLM client with zero mandatory third-party SDK dependencies.

Supports Gemini, Anthropic, OpenAI-compatible APIs, local Ollama, and a
deterministic offline provider for pipeline self-tests.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class LLMError(RuntimeError):
    """Raised when an LLM API call fails or returns invalid data."""


class LLMClient(Protocol):
    """Minimal protocol implemented by all HarborForge LLM backends."""

    model_label: str

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> str:
        """Return raw text response from the model."""


@dataclass(slots=True)
class GeminiClient:
    """Google Gemini REST API client reading GEMINI_API_KEY from environment."""

    model: str = "gemini-2.5-pro"
    api_key: str | None = None
    timeout_sec: float = 120.0

    @property
    def model_label(self) -> str:
        return f"gemini:{self.model}"

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> str:
        key = self.api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY environment variable is not set.")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={key}"
        )
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        data = _post_json(url, payload, headers={}, timeout_sec=self.timeout_sec)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected Gemini response payload: {data}") from exc


@dataclass(slots=True)
class AnthropicClient:
    """Anthropic Messages API client reading ANTHROPIC_API_KEY from environment."""

    model: str = "claude-sonnet-4-20250514"
    api_key: str | None = None
    timeout_sec: float = 120.0

    @property
    def model_label(self) -> str:
        return f"anthropic:{self.model}"

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> str:
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY environment variable is not set.")

        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
        data = _post_json(url, payload, headers=headers, timeout_sec=self.timeout_sec)
        try:
            return "".join(
                block.get("text", "")
                for block in data["content"]
                if block.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Anthropic response payload: {data}") from exc


@dataclass(slots=True)
class OpenAICompatibleClient:
    """OpenAI-compatible chat completions client (OpenAI, OpenRouter, vLLM, etc.)."""

    model: str = "gpt-4o"
    base_url: str | None = None
    api_key: str | None = None
    timeout_sec: float = 120.0

    @property
    def model_label(self) -> str:
        return f"openai:{self.model}"

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> str:
        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("OPENAI_API_KEY environment variable is not set.")
        root = (self.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        url = f"{root}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        data = _post_json(
            url,
            payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout_sec=self.timeout_sec,
        )
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected OpenAI-compatible response payload: {data}") from exc


@dataclass(slots=True)
class OllamaClient:
    """Local Ollama REST client requiring no cloud API keys."""

    model: str = "qwen2.5-coder:14b"
    host: str | None = None
    timeout_sec: float = 180.0

    @property
    def model_label(self) -> str:
        return f"ollama:{self.model}"

    def complete(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> str:
        base = (self.host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        url = f"{base}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        data = _post_json(url, payload, headers={}, timeout_sec=self.timeout_sec)
        try:
            return str(data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Unexpected Ollama response payload: {data}") from exc


def build_llm_client(provider: str, model: str | None = None) -> LLMClient:
    """Construct an LLM client for the requested provider name."""
    normalized = provider.strip().lower()
    if normalized == "gemini":
        return GeminiClient(model=model or "gemini-2.5-pro")
    if normalized == "anthropic":
        return AnthropicClient(model=model or "claude-sonnet-4-20250514")
    if normalized == "openai":
        return OpenAICompatibleClient(model=model or "gpt-4o")
    if normalized == "ollama":
        return OllamaClient(model=model or "qwen2.5-coder:14b")
    raise ValueError(
        f"Unsupported LLM provider '{provider}'. Choose from: gemini, anthropic, openai, ollama, offline."
    )


def extract_json_object(raw_text: str) -> dict[str, object]:
    """Parse a JSON object from raw model output, stripping optional Markdown fences."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            cleaned = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model output contained malformed JSON: {exc}") from exc

    raise LLMError("No valid JSON object found in model output.")


def _post_json(
    url: str,
    payload: dict[str, object],
    *,
    headers: dict[str, str],
    timeout_sec: float,
) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json", **headers}
    request = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise LLMError(f"HTTP {exc.code} calling {url}: {err_body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Network error calling {url}: {exc.reason}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Invalid JSON response from {url}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"Expected JSON object from {url}, got {type(parsed).__name__}")
    return parsed
