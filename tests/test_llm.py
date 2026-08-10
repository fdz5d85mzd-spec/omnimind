"""LLM abstraction tests: provider selection, error surfacing, no fake
answers when unconfigured."""

import json
from unittest.mock import patch

import pytest

from omni.agents.llm import LLMError, LLMNotConfigured, call_llm


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
