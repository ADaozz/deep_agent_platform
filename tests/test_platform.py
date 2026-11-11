import copy
import importlib
import zipfile

import pytest
from fastapi.testclient import TestClient

from platform_app import domain as d


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'DATA', tmp_path)
    from platform_app import main
    d.init()
    return TestClient(main.app)


def test_global_settings_conflict_and_removed_workspace_routes(client):
    config = client.get('/api/settings').json()
    config['system_prompt'] = 'Global instructions'
    config['tools'][0]['description'] = 'Custom docstring'
    assert client.put('/api/settings', json=config).status_code == 200
    assert client.put('/api/settings', json=config).status_code == 409
    saved = client.get('/api/settings').json()
    assert saved['system_prompt'] == 'Global instructions'
    assert saved['tools'][0]['description'] == 'Custom docstring'
    assert d.all_records('workspace') == []
    assert client.get('/api/workspaces').status_code == 404
    assert client.post('/api/workspaces', json={'name': 'Removed'}).status_code == 404
    assert client.put('/api/workspaces/office', json=config).status_code == 404


def test_snapshot_and_tool_scoping(client):
    source = d.get('settings', 'global')
    adapter = d.ToolAdapter(source, 'test')
    source['tools'][0]['enabled'] = False
    assert adapter.config['tools'][0]['enabled']
    other = d.ToolAdapter(d.get('settings', 'global'), 'test')
    with pytest.raises(ValueError):
        other.execute('create_docx', {'title':'x','paragraphs':[]})


@pytest.mark.parametrize('name,args,entry', [
    ('create_xlsx', {'title':'预算','rows':[['费用',100],['=1+1',200]]}, 'xl/worksheets/sheet1.xml'),
])
def test_office_files(client, name, args, entry):
    adapter = d.ToolAdapter(d.get('settings', 'global'), 'files')
    result = adapter.execute(name, args)
    with zipfile.ZipFile(adapter.root / result['filename']) as archive:
        assert entry in archive.namelist()
        assert '内容' in archive.read(entry).decode() if name != 'create_xlsx' else '<f>' not in archive.read(entry).decode()


def test_validation_and_missing_provider(client, monkeypatch):
    monkeypatch.delenv('PLATFORM_API_KEY', raising=False)
    assert client.post('/api/agent/sessions',json={}).status_code == 503
    config = d.get('settings', 'global')
    config['tools'][0]['name'] = 'execute'
    assert client.put('/api/settings',json=config).status_code == 422
    assert client.get('/api/tasks/nonexistent/artifacts/a.docx').status_code == 404






def model_configuration(api='responses', key='private-test-key'):
    return {'default':'provider/demo','timeout':75,'models':{'provider':{'base_url':'https://example.com/v1','api_key':key,'api':api,'reasoning_efforts':['low','high'],'models':{'demo':{'context_window':128000,'input':['text']}}}}}


def test_all_files_lists_owned_sessions_newest_first(client, monkeypatch):
    import os
    from platform_app import itam_bridge
    assert client.put('/api/models', json={'llm': model_configuration(), 'revision': 0}).status_code == 200
    ids = []
    for name in ('first.txt', 'second.txt', 'other.txt'):
        sid = client.post('/api/agent/sessions', json={'model_id': 'provider/demo'}).json()['id']
        assert client.post(f'/api/agent/sessions/{sid}/uploads', files={'file': (name, b'x', 'text/plain')}).status_code == 201
        ids.append(sid)
    for index, sid in enumerate(ids):
        record = d.get('session', sid)
        record['owner_user_id'] = '1002' if index == 2 else '1001'
        record['messages'] = [{'role': 'user', 'content': f'task {index}\ndetails'}]
        d.put('session', record)
        from platform_app.workspace import resolve
        for path in resolve(sid).files_root.rglob('*'):
            if path.is_file():
                os.utime(path, (1000 + index, 1000 + index))

    listing = client.get('/api/agent/files').json()
    assert [item['session_id'] for item in listing] == list(reversed(ids))
    assert listing[0]['session_title'] == 'task 2' and listing[0]['modified_at'] == 1002

    monkeypatch.setattr(itam_bridge, 'is_enabled', lambda: True)
    monkeypatch.setattr(itam_bridge, 'owner', lambda: '1001')
    owned = client.get('/api/agent/files').json()
    assert [item['session_id'] for item in owned] == [ids[1], ids[0]]


def test_delete_session_removes_records_and_storage(client):
    from platform_app.workspace import resolve
    assert client.put('/api/models', json={'llm': model_configuration(), 'revision': 0}).status_code == 200
    session_id = client.post('/api/agent/sessions', json={'model_id': 'provider/demo'}).json()['id']
    assert client.post(f'/api/agent/sessions/{session_id}/uploads', files={'file': ('note.txt', b'hello', 'text/plain')}).status_code == 201
    context = resolve(session_id)
    assert any(context.files_root.iterdir())

    record = d.get('session', session_id)
    record['status'] = 'running'
    d.put('session', record)
    assert client.delete(f'/api/agent/sessions/{session_id}').status_code == 409
    record['status'] = 'completed'
    d.put('session', record)

    assert client.delete(f'/api/agent/sessions/{session_id}').status_code == 204
    assert client.get(f'/api/agent/sessions/{session_id}').status_code == 404
    assert client.delete(f'/api/agent/sessions/{session_id}').status_code == 404
    assert all(item.get('thread_id') != session_id for item in d.all_records('upload'))
    assert all(item['id'] != session_id for item in d.all_records('thread_workspace'))
    assert all(item.get('session_id') != session_id for item in d.all_records('event'))
    assert not context.workspace_root.exists() and not context.session_root.exists()


def test_model_config_redaction_revision_and_persistence(client):
    from platform_app import models
    response = client.put('/api/models',json={'llm':model_configuration(),'revision':0})
    assert response.status_code == 200, response.text
    assert 'private-test-key' not in response.text
    value = response.json()
    assert value['profiles'][0]['reasoning_efforts'] == ['low','high']
    assert value['profiles'][0]['api'] == 'responses'
    assert client.get('/api/models').json() == value
    assert 'private-test-key' not in (d.DATA / 'platform.sqlite').read_bytes().decode(errors='ignore')
    value['llm']['timeout'] = 80
    assert client.put('/api/models',json={'llm':value['llm'],'revision':1}).status_code == 200
    assert models.current()[0]['models']['provider']['api_key'] == 'private-test-key'
    assert client.put('/api/models',json={'llm':value['llm'],'revision':1}).status_code == 409


@pytest.mark.parametrize('api', ['responses','chat_completions'])
def test_core_model_protocol_and_effort(client, api):
    from platform_app.agent_core.llm import build_chat_model
    from platform_app.agent_core.config import Settings
    config = model_configuration(api)
    parsed = Settings.from_mapping({'llm':config})
    model = build_chat_model(parsed.active_profile, reasoning_effort='high', streaming=False)
    assert model.model_name == 'demo'
    if api == 'responses':
        assert model.reasoning == {'effort':'high'}
        assert model.use_responses_api
    else:
        assert model.reasoning_effort == 'high'
        assert model.use_responses_api is False
    model.http_client.close()


def test_session_model_snapshot_and_invalid_effort(client, monkeypatch):
    from platform_app import models
    assert client.put('/api/models',json={'llm':model_configuration(),'revision':0}).status_code == 200
    bad = client.post('/api/agent/sessions',json={'model_id':'provider/demo','reasoning_effort':'xhigh'})
    assert bad.status_code == 422
    task = client.post('/api/agent/sessions',json={'model_id':'provider/demo','reasoning_effort':'high'})
    assert task.status_code == 201, task.text
    task = task.json()
    assert 'model_snapshot' not in task
    assert task['model_id'] == 'provider/demo' and task['reasoning_effort'] == 'high'
    edited = model_configuration('chat_completions')
    client.put('/api/models',json={'llm':edited,'revision':1})
    stored = d.get('session',task['id'])
    frozen = models.unseal(stored['model_snapshot']['encrypted'])
    assert frozen['models']['provider']['api'] == 'responses'
    assert 'model_snapshot' not in client.get('/api/agent/sessions/'+task['id']).json()
    assert 'private-test-key' not in client.get('/api/agent/sessions').text


def test_env_reference_and_invalid_default(client, monkeypatch):
    monkeypatch.delenv('TEST_MODEL_KEY',raising=False)
    config=model_configuration(key='${TEST_MODEL_KEY}')
    response=client.put('/api/models',json={'llm':config,'revision':0})
    assert response.status_code == 200
    assert not response.json()['profiles'][0]['configured']
    assert client.post('/api/agent/sessions',json={}).status_code == 422
    config['default']='provider/missing'
    assert client.put('/api/models',json={'llm':config,'revision':1}).status_code == 422


def test_cli_reasoning_default_and_explicit_native_default(client, monkeypatch):
    from platform_app import models, runtime
    monkeypatch.setattr(runtime.POOL, 'submit', lambda *args: None)
    config = model_configuration()
    config['models']['provider']['reasoning_efforts'] = ['high', 'low']
    value = client.put('/api/models', json={'llm': config, 'revision': 0}).json()
    assert value['default_reasoning_effort'] == 'high'
    assert value['profiles'][0]['default_reasoning_effort'] == 'high'
    assert models.selection()['reasoning_effort'] == 'high'
    assert models.selection(effort=None)['reasoning_effort'] is None
    session = client.post('/api/agent/sessions', json={}).json()
    assert session['model_id'] == 'provider/demo'
    assert session['reasoning_effort'] == 'high'
    explicit = client.post('/api/agent/sessions', json={'reasoning_effort': None}).json()
    assert explicit['reasoning_effort'] is None


def test_cli_yaml_config_import(client, tmp_path, monkeypatch):
    from platform_app import models
    import yaml
    config = model_configuration(key='${TEST_MODEL_CONFIG_KEY}')
    config['models']['provider']['reasoning_efforts'] = ['high', 'low']
    path = tmp_path / 'models.yaml'
    path.write_text(yaml.safe_dump({'llm': config}))
    monkeypatch.setenv('PLATFORM_CONFIG', str(path))
    monkeypatch.setenv('TEST_MODEL_CONFIG_KEY', 'private-config-test-key')
    value = client.get('/api/models').json()
    assert value['llm']['default'] == 'provider/demo'
    assert value['default_reasoning_effort'] == 'high'
    assert value['profiles'][0]['configured']
    assert 'private-config-test-key' not in str(value)
    # The UI can save the imported native YAML into encrypted platform storage.
    assert client.put('/api/models', json={'llm': value['llm'], 'revision': 0}).status_code == 200
    path.unlink()
    assert models.current()[0] == config
