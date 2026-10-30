"""Contract checks against DeepSeek's documented Search / Fetch JSON APIs."""
import json

import httpx
import pytest
from platform_app.agent_core.config import Settings
from pydantic import ValidationError

from platform_app import web_tools as web


def settings():
    return Settings.from_mapping({'llm': {'default': 'deepseek/flash', 'models': {
        'deepseek': {'base_url': 'https://api.deepseek.com/v1', 'api_key': 'test-deepseek-key',
                     'api': 'chat_completions', 'models': {'flash': {}}}}}})


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(web.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_search_fetch_contract_and_secrets(monkeypatch):
    calls = []
    def handle(request):
        assert request.headers['authorization'] == 'Bearer test-deepseek-key'
        assert request.headers['content-type'] == 'application/json'
        calls.append((str(request.url), json.loads(request.content)))
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json={'results': [{'title': 'Source', 'url': 'https://example.com', 'summary': 'Verified summary'}]})
        return httpx.Response(200, json={'title': 'Source', 'content': '# Markdown'})
    transport(monkeypatch, handle)
    search, fetch = web.build_web_tools(settings())
    assert search.invoke({'query': 'DeepSeek site:github.com', 'count': 5})['results'][0]['title'] == 'Source'
    assert fetch.invoke({'url': 'https://example.com'})['content'] == '# Markdown'
    assert calls == [
        (web.BASE + '/search', {'query': 'DeepSeek site:github.com', 'count': 5, 'localization': '', 'need_content': False}),
        (web.BASE + '/fetch', {'url': 'https://example.com'})]
    assert 'test-deepseek-key' not in json.dumps(search.args_schema.model_json_schema())


@pytest.mark.parametrize('args', [{'query': ' '}, {'query': 'x' * 129}, {'query': 'x', 'count': 51}, {'query': 'x', 'search_depth': 'advanced'}])
def test_search_validates_native_parameters(args):
    with pytest.raises(ValidationError):
        web.build_web_tools(settings())[0].invoke(args)


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://user:password@example.com', 'https:///missing-host'])
def test_fetch_validates_url(url):
    with pytest.raises(ValidationError):
        web.build_web_tools(settings())[1].invoke({'url': url})


def test_error_body_never_leaks_and_no_fallback(monkeypatch):
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(401, json={'error': {'message': 'test-deepseek-key'}})
    transport(monkeypatch, handle)
    result = web.build_web_tools(settings())[0].invoke({'query': 'test'})
    assert '401' in result and 'test-deepseek-key' not in result
    assert calls == [web.BASE + '/search']


def test_other_provider_key_is_never_sent(monkeypatch):
    transport(monkeypatch, lambda request: pytest.fail('Must not send another provider key'))
    from dataclasses import replace
    cfg = settings()
    cfg = replace(cfg, llm_profiles=(replace(cfg.active_profile, base_url='https://example.com/v1'),))
    assert 'API Key' in web.build_web_tools(cfg)[0].invoke({'query': 'test'})
