"""Client for OpenAI-compatible chat endpoints such as a vLLM server (F-06).

Requests are cached on disk by a hash of the full request payload, so a run
can be replayed exactly (``offline=True`` refuses to make network calls).
Model, revision, decoding parameters and seed are recorded with every
completion.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from srev.llm.prompt import Prompt


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:8000/v1"
    model: str = "Qwen/Qwen2.5-7B-Instruct"
    revision: str | None = Field(None, description="checkpoint revision, recorded in manifests")
    temperature: float = 0.0
    top_p: float = 1.0
    max_tokens: int = 4096
    seed: int | None = 42
    api_key_env: str = "SREV_LLM_API_KEY"
    response_format: Literal["json_schema", "json_object", "none"] = "json_schema"
    timeout_s: float = 300.0
    retries: int = 3
    cache_dir: str | None = None
    offline: bool = False


class Completion(BaseModel):
    content: str
    model: str
    request_hash: str
    prompt_sha256: str
    cached: bool = False
    seconds: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None


class LLMError(RuntimeError):
    pass


class LLMRequestRejected(LLMError):
    """4xx response: retrying the same request will not help."""


class ChatClient:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(config.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._http = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            timeout=config.timeout_s,
            transport=transport,
        )
        self.cache_dir = Path(config.cache_dir) if config.cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self._http.close()

    def payload(self, prompt: Prompt, schema: dict[str, Any] | None) -> dict[str, Any]:
        cfg = self.config
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": prompt.messages(),
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "max_tokens": cfg.max_tokens,
        }
        if cfg.seed is not None:
            body["seed"] = cfg.seed
        if cfg.response_format == "json_schema" and schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "srev_output", "schema": schema},
            }
        elif cfg.response_format in ("json_schema", "json_object"):
            body["response_format"] = {"type": "json_object"}
        return body

    def complete(self, prompt: Prompt, schema: dict[str, Any] | None = None) -> Completion:
        body = self.payload(prompt, schema)
        request_hash = hashlib.sha256(
            json.dumps({**body, "revision": self.config.revision}, sort_keys=True).encode()
        ).hexdigest()
        cache_file = self.cache_dir / f"{request_hash}.json" if self.cache_dir else None
        if cache_file and cache_file.exists():
            cached = Completion.model_validate_json(cache_file.read_text())
            return cached.model_copy(update={"cached": True})
        if self.config.offline:
            raise LLMError(f"offline mode and no cached response for request {request_hash[:12]}")

        start = time.perf_counter()
        data = self._post(body)
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:300]}") from exc
        usage = data.get("usage") or {}
        completion = Completion(
            content=content,
            model=data.get("model", self.config.model),
            request_hash=request_hash,
            prompt_sha256=prompt.sha256,
            seconds=round(time.perf_counter() - start, 3),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            finish_reason=choice.get("finish_reason"),
        )
        if cache_file:
            cache_file.write_text(completion.model_dump_json(indent=2))
        return completion

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        delay = 2.0
        last: Exception | None = None
        for attempt in range(self.config.retries + 1):
            try:
                resp = self._http.post("/chat/completions", json=body)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise LLMError(f"server error {resp.status_code}: {resp.text[:200]}")
                if resp.status_code >= 400:
                    raise LLMRequestRejected(
                        f"request rejected {resp.status_code}: {resp.text[:300]}"
                    )
                result: dict[str, Any] = resp.json()
                return result
            except LLMRequestRejected:
                raise
            except (httpx.TransportError, LLMError) as exc:
                last = exc
                if attempt < self.config.retries:
                    time.sleep(delay)
                    delay *= 2
        raise LLMError(f"giving up after {self.config.retries + 1} attempts: {last}")

    def server_models(self) -> list[str]:
        """Model ids the server reports (``GET /models``); empty on failure."""
        try:
            resp = self._http.get("/models")
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            return []
