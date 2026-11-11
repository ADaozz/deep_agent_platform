"""Per-user model sources: merged with global models, isolated per owner, sealed at rest."""
import pytest

from platform_app import domain as d
from platform_app import models as m


def source(key='sk-personal-secret', base_url='https://llm.example.com/v1'):
    return {'base_url': base_url, 'api_key': key, 'api': 'chat_completions', 'models': {'chat': {}}}


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'DATA', tmp_path)
    for name in ('PLATFORM_CONFIG', 'PLATFORM_MODEL', 'PLATFORM_API_KEY', 'PLATFORM_BASE_URL'):
        monkeypatch.delenv(name, raising=False)


def test_personal_models_are_merged_only_for_their_owner():
    m.save({'default': 'shared/chat', 'timeout': 60, 'models': {'shared': source('sk-global')}}, 0)
    m.save_personal('1001', {'default': 'mine/chat', 'models': {'mine': source()}}, 0)

    own = m.options('1001')
    assert {p['id']: p['scope'] for p in own['profiles']} == {'shared/chat': 'global', 'my-mine/chat': 'personal'}
    assert own['default'] == 'my-mine/chat'
    assert [p['id'] for p in m.options('1002')['profiles']] == ['shared/chat']
    assert [p['id'] for p in m.options(None)['profiles']] == ['shared/chat']

    assert m.selection('my-mine/chat', owner='1001')['id'] == 'my-mine/chat'
    with pytest.raises(LookupError):
        m.selection('my-mine/chat', owner='1002')


def test_personal_secret_is_masked_sealed_and_restorable():
    m.save_personal('1001', {'default': 'mine/chat', 'models': {'mine': source()}}, 0)
    public = m.public_personal('1001')
    assert public['llm']['models']['mine']['api_key'] == m.MASK
    assert 'sk-personal-secret' not in (d.DATA / 'platform.sqlite').read_bytes().decode(errors='ignore')

    m.save_personal('1001', public['llm'], public['revision'])
    config, revision = m.personal_current('1001')
    assert config['models']['mine']['api_key'] == 'sk-personal-secret' and revision == 2

    with pytest.raises(RuntimeError):
        m.save_personal('1001', public['llm'], 0)
    m.save_personal('1001', {'default': '', 'models': {}}, 2)
    assert m.public_personal('1001')['profiles'] == []


def test_personal_models_cannot_use_server_credentials_or_odd_endpoints():
    with pytest.raises(ValueError, match='环境变量'):
        m.save_personal('1001', {'default': 'mine/chat', 'models': {'mine': source('${DEEPSEEK_API_KEY}')}}, 0)
    with pytest.raises(ValueError, match='base_url'):
        m.save_personal('1001', {'default': 'mine/chat', 'models': {'mine': source(base_url='file:///etc/passwd')}}, 0)
    with pytest.raises(ValueError, match='default'):
        m.save_personal('1001', {'default': 'mine/chat', 'timeout': 5, 'models': {'mine': source()}}, 0)
    with pytest.raises(ValueError):
        m.save_personal('1001', {'default': 'mine/chat', 'models': {'mine': {**source(), 'api_key': m.MASK}}}, 0)


def test_personal_source_may_not_shadow_global_source():
    m.save({'default': 'my-x/chat', 'timeout': 60, 'models': {'my-x': source('sk-global')}}, 0)
    with pytest.raises(ValueError, match='重名'):
        m.save_personal('1001', {'default': 'x/chat', 'models': {'x': source()}}, 0)
