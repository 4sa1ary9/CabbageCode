from types import SimpleNamespace

from cabbagecode.client import _compat_cache_read


def test_cache_read_from_standard_field():
    usage = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=50), prompt_tokens=200)
    assert _compat_cache_read(usage) == 50


def test_cache_read_from_deepseek_field():
    usage = SimpleNamespace(prompt_tokens_details=None, prompt_cache_hit_tokens=120, prompt_tokens=300)
    assert _compat_cache_read(usage) == 120


def test_cache_read_defaults_to_zero():
    assert _compat_cache_read(SimpleNamespace(prompt_tokens=10)) == 0


import pytest

from cabbagecode.client import OpenAICompatClient
from cabbagecode.config import ProviderConfig
from cabbagecode.conversation import ConversationManager
from cabbagecode.tools.base import StreamEnd


def _chunk(content="", finish=None, usage=None, empty=False):
    choices = [] if empty else [SimpleNamespace(
        delta=SimpleNamespace(content=content, tool_calls=None), finish_reason=finish)]
    return SimpleNamespace(choices=choices, usage=usage)


async def _stream_end(chunks):
    client = OpenAICompatClient(ProviderConfig(name="t", protocol="openai-compat",
                                               base_url="http://127.0.0.1:9", model="m", api_key="x"))

    async def fake_create(**kwargs):
        async def gen():
            for c in chunks:
                yield c
        return gen()

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    conv = ConversationManager()
    conv.add_user_message("hi")
    ends = [e async for e in client.stream(conv) if isinstance(e, StreamEnd)]
    assert len(ends) == 1
    return ends[0]


@pytest.mark.asyncio
async def test_usage_attached_to_last_choice_chunk_deepseek_style():
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=7, prompt_tokens_details=None,
                            prompt_cache_hit_tokens=60)
    end = await _stream_end([_chunk("OK"), _chunk("", finish="stop", usage=usage)])
    assert (end.input_tokens, end.output_tokens, end.cache_read) == (40, 7, 60)


@pytest.mark.asyncio
async def test_usage_in_trailing_empty_chunk_openai_style():
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=5,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=20))
    end = await _stream_end([_chunk("OK"), _chunk("", finish="stop"), _chunk(empty=True, usage=usage)])
    assert (end.input_tokens, end.output_tokens, end.cache_read) == (30, 5, 20)
