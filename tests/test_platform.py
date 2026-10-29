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


def test_workspace_edit_isolation_and_conflict(client):
    before = client.get('/api/workspaces').json()
    office = copy.deepcopy(before[0])
    office['system_prompt'] = 'Only this office'
    office['tools'][0]['description'] = 'Custom docstring'
    assert client.put('/api/workspaces/office', json=office).status_code == 200
    assert client.put('/api/workspaces/office', json=office).status_code == 409
    after = client.get('/api/workspaces').json()
    assert after[1:] == before[1:]
    assert after[0]['tools'][0]['description'] == 'Custom docstring'


def test_snapshot_and_tool_scoping(client):
    source = d.get('workspace', 'office')
    adapter = d.WorkspaceAdapter(source, 'test')
    source['tools'][0]['enabled'] = False
    assert adapter.config['tools'][0]['enabled']
    other = d.WorkspaceAdapter(d.get('workspace', 'research'), 'test')
    with pytest.raises(ValueError):
        other.execute('create_docx', {'title':'x','paragraphs':[]})


@pytest.mark.parametrize('name,args,entry', [
    ('create_docx', {'title':'计划','paragraphs':['内容']}, 'word/document.xml'),
    ('create_pptx', {'title':'发布','slides':[{'title':'目标','body':'内容'}]}, 'ppt/slides/slide2.xml'),
    ('create_xlsx', {'title':'预算','rows':[['费用',100],['=1+1',200]]}, 'xl/worksheets/sheet1.xml'),
])
def test_office_files(client, name, args, entry):
    adapter = d.WorkspaceAdapter(d.get('workspace','office'), 'files')
    result = adapter.execute(name, args)
    with zipfile.ZipFile(adapter.root / result['filename']) as archive:
        assert entry in archive.namelist()
        assert '内容' in archive.read(entry).decode() if name != 'create_xlsx' else '<f>' not in archive.read(entry).decode()


def test_validation_and_missing_provider(client, monkeypatch):
    monkeypatch.delenv('PLATFORM_API_KEY', raising=False)
    assert client.post('/api/workspaces/office/tasks',json={'prompt':'make a doc'}).status_code == 503
    config = d.get('workspace','office')
    config['tools'][0]['name'] = 'execute'
    assert client.put('/api/workspaces/office',json=config).status_code == 422
    assert client.get('/api/tasks/nonexistent/artifacts/a.docx').status_code == 404


def test_task_file_download(client):
    d.put('task', dict(id='download',workspace_id='office',status='completed'))
    adapter = d.WorkspaceAdapter(d.get('workspace','office'),'download')
    result = adapter.execute('create_docx',dict(title='Title',paragraphs=['Body']))
    response = client.get('/api/tasks/download/artifacts/' + result['filename'])
    assert response.status_code == 200
    assert response.content[:2] == b'PK'
    assert client.get('/api/tasks/download').json()['artifacts'] == [result['filename']]


def test_real_core_executes_workspace_tool(client):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from platform_app.main import run_task

    class ScriptedModel(BaseChatModel):
        @property
        def _llm_type(self):
            return 'scripted-integration-test'

        def bind_tools(self, tools, **kwargs):
            names = [t.get('name') if isinstance(t, dict) else t.name for t in tools]
            assert 'create_docx' in names
            assert 'execute' in names
            assert 'read_file' in names
            assert 'task' in names
            assert 'write_todos' not in names
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if any(isinstance(m, ToolMessage) for m in messages):
                msg = AIMessage(content='文档已生成。')
            else:
                msg = AIMessage(content='', tool_calls=[{'name':'create_docx','args':{'title':'测试','paragraphs':['来自真实内核的工具调用']},'id':'office-call','type':'tool_call'}])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    record = dict(id='core-test',workspace_id='office',prompt='生成文档',config=d.get('workspace','office'),status='queued',output='',error=None,revision=1)
    run_task(record, model=ScriptedModel(), raise_errors=True)
    saved = d.get('task','core-test')
    assert saved['status'] == 'completed', saved
    assert '文档已生成' in saved['output']
    assert list((d.DATA / 'artifacts' / 'office' / 'core-test').glob('*.docx'))


def model_configuration(api='responses', key='private-test-key'):
    return {'default':'provider/demo','timeout':75,'models':{'provider':{'base_url':'https://example.com/v1','api_key':key,'api':api,'reasoning_efforts':['low','high'],'models':{'demo':{'context_window':128000,'input':['text']}}}}}


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
    from agent.llm import build_chat_model
    from agent.config import Settings
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


def test_task_model_snapshot_and_invalid_effort(client, monkeypatch):
    from platform_app import main, models
    monkeypatch.setattr(main.POOL, 'submit', lambda *args: None)
    assert client.put('/api/models',json={'llm':model_configuration(),'revision':0}).status_code == 200
    bad = client.post('/api/workspaces/office/tasks',json={'prompt':'test','model_id':'provider/demo','reasoning_effort':'xhigh'})
    assert bad.status_code == 422
    task = client.post('/api/workspaces/office/tasks',json={'prompt':'test','model_id':'provider/demo','reasoning_effort':'high'})
    assert task.status_code == 202, task.text
    task = task.json()
    assert 'model_snapshot' not in task
    assert task['model_id'] == 'provider/demo' and task['reasoning_effort'] == 'high'
    edited = model_configuration('chat_completions')
    client.put('/api/models',json={'llm':edited,'revision':1})
    stored = d.get('task',task['id'])
    frozen = models.unseal(stored['model_snapshot']['encrypted'])
    assert frozen['models']['provider']['api'] == 'responses'
    assert 'model_snapshot' not in client.get('/api/tasks/'+task['id']).json()
    assert 'private-test-key' not in client.get('/api/tasks').text


def test_env_reference_and_invalid_default(client, monkeypatch):
    monkeypatch.delenv('TEST_MODEL_KEY',raising=False)
    config=model_configuration(key='${TEST_MODEL_KEY}')
    response=client.put('/api/models',json={'llm':config,'revision':0})
    assert response.status_code == 200
    assert not response.json()['profiles'][0]['configured']
    assert client.post('/api/workspaces/office/tasks',json={'prompt':'test'}).status_code == 422
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
    session = client.post('/api/agent/sessions', json={'workspace_id': 'writing'}).json()
    assert session['model_id'] == 'provider/demo'
    assert session['reasoning_effort'] == 'high'
    explicit = client.post('/api/agent/sessions', json={'workspace_id': 'writing', 'reasoning_effort': None}).json()
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
