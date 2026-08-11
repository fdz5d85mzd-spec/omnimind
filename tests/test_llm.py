"""LLM abstraction tests: provider selection, error surfacing, no fake
answers when unconfigured."""

import json
from unittest.mock import patch

import pytest

from omni.agents.llm import LLMError, LLMNotConfigured, call_llm, stream_llm


def test_no_key_raises_not_configured():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(LLMNotConfigured):
            call_llm("hello")


def test_anthropic_used_when_both_keys_set():
    env = {"ANTHROPIC_API_KEY": "ak", "OPENAI_API_KEY": "ok"}
    with patch.dict("os.environ", env, clear=True):
        with patch("omni.agents.llm._call_anthropic", return_value="from anthropic") as m_anth:
            with patch("omni.agents.llm._call_openai") as m_openai:
                result = call_llm("hi")
    assert result == "from anthropic"
    m_anth.assert_called_once()
    m_openai.assert_not_called()


def test_openai_used_when_only_openai_key_set():
    with patch.dict("os.environ", {"OPENAI_API_KEY": "ok"}, clear=True):
        with patch("omni.agents.llm._call_openai", return_value="from openai") as m_openai:
            result = call_llm("hi")
    assert result == "from openai"
    m_openai.assert_called_once()


def test_user_supplied_openai_key_wins_over_org_anthropic_key():
    # "Bring your own key" always takes priority, even when the org has its
    # own Anthropic key configured -- it's the user's own quota, not ours.
    env = {"ANTHROPIC_API_KEY": "ak", "OPENAI_API_KEY": "ok"}
    with patch.dict("os.environ", env, clear=True):
        with patch("omni.agents.llm._call_openai", return_value="from user's own key") as m_openai:
            with patch("omni.agents.llm._call_anthropic") as m_anth:
                result = call_llm("hi", user_api_key="sk-user-own-key", user_provider="openai")
    assert result == "from user's own key"
    m_openai.assert_called_once_with("hi", "", 1200, 60.0, api_key="sk-user-own-key")
    m_anth.assert_not_called()


def test_user_supplied_key_ignored_for_unsupported_provider():
    # Only "openai" is a recognized BYOK provider today -- anything else
    # falls back to normal org-key selection instead of silently no-op'ing.
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "ak"}, clear=True):
        with patch("omni.agents.llm._call_anthropic", return_value="from org key") as m_anth:
            result = call_llm("hi", user_api_key="sk-user-own-key", user_provider="gemini")
    assert result == "from org key"
    m_anth.assert_called_once()


def test_stream_user_supplied_openai_key_wins():
    lines = _sse({"choices": [{"delta": {"content": "hi"}}]})
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "ak"}, clear=True):
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(lines)) as m_open:
            chunks = list(stream_llm("hi", user_api_key="sk-user-own-key", user_provider="openai"))
    assert chunks == ["hi"]
    # Hit the OpenAI endpoint (not Anthropic's) using the user's key in the header.
    sent_req = m_open.call_args[0][0]
    assert sent_req.full_url == "https://api.openai.com/v1/chat/completions"
    assert sent_req.headers["Authorization"] == "Bearer sk-user-own-key"


class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_openai_parses_response_content():
    payload = {"choices": [{"message": {"content": "Paris."}}]}
    with patch.dict("os.environ", {"OPENAI_API_KEY": "ok"}, clear=True):
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(json.dumps(payload).encode())):
            result = call_llm("capital of France?")
    assert result == "Paris."


def test_anthropic_parses_response_content():
    payload = {"content": [{"type": "text", "text": "Paris."}]}
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "ak"}, clear=True):
        with patch("urllib.request.urlopen", return_value=_FakeHTTPResponse(json.dumps(payload).encode())):
            result = call_llm("capital of France?")
    assert result == "Paris."


def test_http_error_becomes_llm_error():
    import io
    import urllib.error

    err = urllib.error.HTTPError("url", 401, "unauthorized", None, io.BytesIO(b'{"error":"bad key"}'))
    with patch.dict("os.environ", {"OPENAI_API_KEY": "ok"}, clear=True):
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(LLMError):
                call_llm("hi")


class _FakeSSEResponse:
    """Iterable of SSE `data: {...}` lines, matching what urlopen() returns
    for a streaming request (an iterable, closeable file-like object)."""

    def __init__(self, lines: list[bytes]):
        self._lines = lines
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        self.closed = True


def _sse(*payloads: dict) -> list[bytes]:
    return [f"data: {json.dumps(p)}\n".encode() for p in payloads] + [b"data: [DONE]\n"]


def test_stream_no_key_raises_not_configured():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(LLMNotConfigured):
            next(stream_llm("hello"))


def test_stream_anthropic_yields_text_deltas_in_order():
    lines = _sse(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Par"}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "is."}},
        {"type": "message_stop"},
    )
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "ak"}, clear=True):
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(lines)):
            chunks = list(stream_llm("capital of France?"))
    assert chunks == ["Par", "is."]


def test_stream_openai_yields_text_deltas_in_order():
    lines = _sse(
        {"choices": [{"delta": {"content": "Par"}}]},
        {"choices": [{"delta": {"content": "is."}}]},
    )
    with patch.dict("os.environ", {"OPENAI_API_KEY": "ok"}, clear=True):
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(lines)):
            chunks = list(stream_llm("capital of France?"))
    assert chunks == ["Par", "is."]


def test_stream_closes_response_when_exhausted():
    lines = _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}})
    fake = _FakeSSEResponse(lines)
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "ak"}, clear=True):
        with patch("urllib.request.urlopen", return_value=fake):
            list(stream_llm("hello"))
    assert fake.closed is True


def test_stream_http_error_becomes_llm_error():
    import io
    import urllib.error

    err = urllib.error.HTTPError("url", 401, "unauthorized", None, io.BytesIO(b"bad key"))
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "ak"}, clear=True):
        with patch("urllib.request.urlopen", side_effect=err):
            with pytest.raises(LLMError):
                list(stream_llm("hi"))
