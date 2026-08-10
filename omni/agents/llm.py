"""LLM abstraction for OmniMind's agent runner.

Supports OpenAI or Anthropic, configured via OPENAI_API_KEY /
ANTHROPIC_API_KEY (Anthropic wins if both are set). No key configured is a
real, reportable state (LLMNotConfigured) — the runner surfaces it to the
caller rather than returning a fabricated answer.

Uses the standard library's urllib instead of adding a new runtime HTTP
dependency; this module makes at most one blocking call per invocation,
called from a sync FastAPI route (already off the event loop).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Iterator


class LLMNotConfigured(RuntimeError):
    """Neither OPENAI_API_KEY nor ANTHROPIC_API_KEY is set."""


class LLMError(RuntimeError):
    """The configured LLM provider returned an error or was unreachable."""


def call_llm(prompt: str, system: str = "", max_tokens: int = 1200, timeout: float = 60.0) -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _call_anthropic(prompt, system, max_tokens, timeout)
    if os.environ.get("OPENAI_API_KEY"):
        return _call_openai(prompt, system, max_tokens, timeout)
    raise LLMNotConfigured("Set OPENAI_API_KEY or ANTHROPIC_API_KEY to enable the agent runner.")


def stream_llm(prompt: str, system: str = "", max_tokens: int = 1200, timeout: float = 90.0) -> Iterator[str]:
    """Yield the answer as it's generated (text deltas), instead of waiting
    for the full completion — this is what makes the chat UI feel like
    Claude/DeepSeek instead of a blocking spinner."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        yield from _stream_anthropic(prompt, system, max_tokens, timeout)
    elif os.environ.get("OPENAI_API_KEY"):
        yield from _stream_openai(prompt, system, max_tokens, timeout)
    else:
        raise LLMNotConfigured("Set OPENAI_API_KEY or ANTHROPIC_API_KEY to enable the agent runner.")


def _post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"LLM provider returned {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"LLM provider unreachable: {e}") from e


def _call_openai(prompt: str, system: str, max_tokens: int, timeout: float) -> str:
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    data = _post_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        {"model": model, "messages": messages, "max_tokens": max_tokens},
        timeout,
    )
    return data["choices"][0]["message"]["content"]


def _call_anthropic(prompt: str, system: str, max_tokens: int, timeout: float) -> str:
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    body: dict = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        body,
        timeout,
    )
    return "".join(part.get("text", "") for part in data.get("content", []))


def _open_sse_stream(url: str, headers: dict, body: dict, timeout: float):
    req = urllib.request.Request(
        url,
        data=json.dumps({**body, "stream": True}).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"LLM provider returned {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"LLM provider unreachable: {e}") from e


def _sse_events(resp) -> Iterator[dict]:
    """Parse an SSE byte stream into decoded JSON `data:` payloads."""
    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data or data == "[DONE]":
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue
    except urllib.error.URLError as e:
        raise LLMError(f"LLM provider connection dropped mid-stream: {e}") from e
    finally:
        resp.close()


def _stream_anthropic(prompt: str, system: str, max_tokens: int, timeout: float) -> Iterator[str]:
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    body: dict = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    resp = _open_sse_stream(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        body,
        timeout,
    )
    for evt in _sse_events(resp):
        if evt.get("type") == "content_block_delta":
            text = evt.get("delta", {}).get("text")
            if text:
                yield text
        elif evt.get("type") == "error":
            raise LLMError(f"Anthropic stream error: {evt.get('error')}")


def _stream_openai(prompt: str, system: str, max_tokens: int, timeout: float) -> Iterator[str]:
    model = os.environ.get("OPENAI_MODEL", "gpt-4o")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    resp = _open_sse_stream(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        {"model": model, "messages": messages, "max_tokens": max_tokens},
        timeout,
    )
    for evt in _sse_events(resp):
        choices = evt.get("choices") or []
        if not choices:
            continue
        text = choices[0].get("delta", {}).get("content")
        if text:
            yield text
