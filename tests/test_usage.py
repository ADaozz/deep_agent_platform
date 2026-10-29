import pytest
from langchain_core.messages import AIMessage

from platform_app.usage import cache_usage


@pytest.mark.parametrize('cached,expected', [(0, 0.0), (256, 256 / 4599), (4599, 1.0), (4600, None), (-1, None)])
def test_cache_ratio_uses_reported_input_tokens(cached, expected):
    message = AIMessage(content='answer', usage_metadata={'input_tokens': 4599, 'output_tokens': 100, 'total_tokens': 4699, 'input_token_details': {'cache_read': cached}}, response_metadata={'deep_agent_model_id': 'deepseek/flash'})
    value = cache_usage({'input_tokens': 4599}, [message], 'deepseek/flash')
    assert value['cache_hit_rate'] == expected


@pytest.mark.parametrize('raw', [
    {'prompt_tokens': 1000, 'prompt_cache_hit_tokens': 800, 'prompt_cache_miss_tokens': 200},
    {'prompt_tokens': 1000, 'prompt_tokens_details': {'cached_tokens': 800}},
    {'input_tokens': 1000, 'input_tokens_details': {'cached_tokens': 800}},
])
def test_native_deepseek_and_compatible_response_fields(raw):
    message = AIMessage(content='answer', response_metadata={'deep_agent_model_id': 'deepseek/flash', 'token_usage': raw})
    assert cache_usage({'input_tokens': 1000}, [message], 'deepseek/flash')['cache_hit_rate'] == 0.8


def test_missing_usage_zero_input_and_model_identity():
    message = AIMessage(content='answer', usage_metadata={'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'input_token_details': {'cache_read': 0}}, response_metadata={'deep_agent_model_id': 'deepseek/flash'})
    assert cache_usage({'input_tokens': 0}, [message], 'deepseek/flash')['cache_hit_rate'] is None
    assert cache_usage({'input_tokens': 1000}, [message], 'other/model')['cache_hit_rate'] is None
    assert cache_usage({}, [message], 'deepseek/flash')['cache_hit_rate'] is None
    assert cache_usage({'input_tokens': 1000}, [], 'deepseek/flash')['cache_hit_rate'] is None
    invalid = AIMessage(content='answer', response_metadata={'deep_agent_model_id': 'deepseek/flash', 'token_usage': {'prompt_tokens': 1000, 'prompt_cache_hit_tokens': True}})
    assert cache_usage({'input_tokens': 1000}, [invalid], 'deepseek/flash')['cache_hit_rate'] is None
