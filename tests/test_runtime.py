"""Exercise the HTTP adapter against the real core and real Bubblewrap tools."""

import pytest
import shlex
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from platform_app import domain as d, models as m, runtime as rt
from platform_app.main import app


class CoreModel(BaseChatModel):
    target: str

    @property
    def _llm_type(self):
        return 'core-adapter-integration'

    def bind_tools(self, tools, **kwargs):
        names = [t.get('name') if isinstance(t, dict) else t.name for t in tools]
        assert 'execute' in names
        assert 'write_file' in names
        assert 'write_todos' not in names
        assert 'read_file' not in names  # workspace toggle reaches native model tools
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        last_user = max(i for i, message in enumerate(messages) if isinstance(message, HumanMessage))
        text = messages[last_user].content
        if text == 'write a file' and not any(isinstance(message, ToolMessage) for message in messages[last_user:]):
            msg = AIMessage(content='', tool_calls=[{'name': 'write_file', 'args': {'file_path': self.target, 'content': 'from the real kernel'}, 'id': 'write-1', 'type': 'tool_call'}])
        elif text == 'run a command' and not any(isinstance(message, ToolMessage) for message in messages[last_user:]):
            msg = AIMessage(content='', tool_calls=[{'name': 'execute', 'args': {'command': 'printf core-command-ok'}, 'id': 'exec-1', 'type': 'tool_call'}])
        elif text == 'ask me a question' and not any(isinstance(message, ToolMessage) for message in messages[last_user:]):
            msg = AIMessage(content='', tool_calls=[{'name': 'request_human_input', 'args': {'reason': 'Need user preference', 'question': 'What title?', 'fields': [{'id': 'title', 'type': 'text', 'label': 'Title', 'required': True}]}, 'id': 'human-1', 'type': 'tool_call'}])
        elif text.startswith('inspect uploaded file') and not any(isinstance(message, ToolMessage) for message in messages[last_user:]):
            path = next(line for line in text.splitlines() if line.startswith('- ')).split(': ', 1)[1]
            msg = AIMessage(content='', tool_calls=[{'name': 'execute', 'args': {'command': 'cat ' + shlex.quote(path)}, 'id': 'upload-read', 'type': 'tool_call'}])
        elif text == 'remember the first request':
            assert any(isinstance(message, HumanMessage) and message.content == 'write a file' for message in messages)
            msg = AIMessage(content='Conversation restored from the native checkpoint.')
        elif text == 'cached answer':
            msg = AIMessage(content='Cached response.', usage_metadata={'input_tokens': 1000, 'output_tokens': 10, 'total_tokens': 1010, 'input_token_details': {'cache_read': 750}})
        else:
            msg = AIMessage(content='Core task complete.')
        return ChatResult(generations=[ChatGeneration(message=msg)])


class DelegationModel(BaseChatModel):
    target: str
    child: bool = False

    @property
    def _llm_type(self):
        return 'native-delegation-integration'

    def bind_tools(self, tools, **kwargs):
        names = [t.get('name') if isinstance(t, dict) else t.name for t in tools]
        assert 'write_todos' not in names
        if 'task' in names:
            assert 'read_subagent_log' in names
            return self.model_copy(update={'child': False})
        assert 'execute' in names
        return self.model_copy(update={'child': True})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        has_result = any(isinstance(m, ToolMessage) for m in messages)
        if self.child and not has_result:
            msg = AIMessage(content='', tool_calls=[{'name': 'execute', 'args': {'command': 'printf delegated-ok > ' + shlex.quote(self.target)}, 'id': 'child-exec', 'type': 'tool_call'}])
        elif self.child:
            msg = AIMessage(content='Subagent wrote delegated-ok.')
        elif not has_result:
            msg = AIMessage(content='', tool_calls=[{'name': 'task', 'args': {'description': 'Write the delegated marker file and report success.', 'subagent_type': 'general-purpose'}, 'id': 'native-delegate', 'type': 'tool_call'}])
        else:
            assert any('Subagent wrote delegated-ok.' in str(m.content) for m in messages if isinstance(m, ToolMessage))
            msg = AIMessage(content='Main agent received subagent result.')
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'DATA', tmp_path)
    d.init()
    rt.RUNTIMES.clear()
    config = {'default': 'test/model', 'models': {'test': {'base_url': 'https://example.com/v1', 'api_key': 'secret-adapter-test', 'api': 'chat_completions', 'reasoning_efforts': ['low', 'high'], 'models': {'model': {}}}}}
    m.save(config, 0)
    ws = d.get('workspace', 'writing')
    ws['core_tools'] = {'read_file': False}
    ws['skills'] = [{'name': 'Test skill', 'description': 'Test reusable workflow', 'content': 'Use precise output.', 'enabled': True}]
    d.put('workspace', ws)
    target = str(tmp_path / 'workspaces' / 'writing' / 'files' / 'hello.txt')
    def build_model(profile, **kwargs):
        model = CoreModel(target=target)
        object.__setattr__(model, '_deep_agent_model_id', profile.id)
        return model
    monkeypatch.setattr('agent.llm.build_chat_model', build_model)
    monkeypatch.setattr('agent.runner.build_chat_model', build_model)
    monkeypatch.setattr(rt.POOL, 'submit', lambda fn, *args: fn(*args))
    yield TestClient(app)
    for runtime in rt.RUNTIMES.values():
        for service in runtime.artifact_services.values():service.close()
        if runtime.runner:
            runtime.runner.close()
            runtime.runner.session_store.close()
    rt.RUNTIMES.clear()


def new_session(client):
    response = client.post('/api/agent/sessions', json={'workspace_id': 'writing', 'model_id': 'test/model'})
    assert response.status_code == 201, response.text
    assert 'secret-adapter-test' not in response.text
    assert 'config' not in response.json()
    return response.json()['id']


def approve(client, session_id):
    record = client.get('/api/agent/sessions/' + session_id).json()
    assert record['status'] == 'waiting_confirmation', record
    decisions = [{'tool_call_id': call['toolCallId'], 'decision': 'approve'} for call in record['pending_tool_calls']]
    return client.post(f'/api/agent/sessions/{session_id}/actions/approve', json={'decisions': decisions})


def test_native_tools_approval_checkpoint_and_skill_mount(client, tmp_path):
    session_id = new_session(client)
    response = client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'write a file'})
    assert response.status_code == 202, response.text
    target = tmp_path / 'workspaces' / 'writing' / 'files' / 'hello.txt'
    assert not target.exists()  # real HITL prevents the actual write
    assert approve(client, session_id).status_code == 202
    saved = client.get('/api/agent/sessions/' + session_id).json()
    assert saved['status'] == 'completed', saved
    assert target.read_text() == 'from the real kernel'
    outputs = [e for e in d.all_records('event') if e['type'] == 'output_files']
    assert any(file['path'] == 'hello.txt' and file['size'] == len('from the real kernel') for event in outputs for file in event['files'])
    runner = rt.RUNTIMES[session_id].runner
    skills = runner.prepared.backend.download_files(['/workspace-skills/skill-1/SKILL.md'])
    assert b'Use precise output.' in skills[0].content
    response = client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'run a command'})
    assert response.status_code == 202
    assert approve(client, session_id).status_code == 202
    journal = d.all_records('event')
    assert any(e['type'] == 'tool_completed' and 'core-command-ok' in e.get('content', '') for e in journal), [(e.get('type'),e.get('name'),e.get('content')) for e in journal if e.get('type') in ('tool_completed','error','result')]
    runner.close()
    runner.session_store.close()
    rt.RUNTIMES.pop(session_id)
    response = client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'remember the first request'})
    assert response.status_code == 202
    saved = client.get('/api/agent/sessions/' + session_id).json()
    assert saved['status'] == 'completed', saved
    assert 'restored' in saved['output']
    assert client.get('/api/agent/workspaces/writing/files/hello.txt').text == 'from the real kernel'
    assert client.get('/api/agent/workspaces/writing/files/%2E%2E/%2E%2E/platform.sqlite').status_code == 404


def test_completed_session_journal_retains_streams_and_persisted_timestamps(client):
    from datetime import datetime
    session_id = new_session(client)
    assert client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'cached answer'}).status_code == 202
    runtime = rt.runtime(session_id)
    runtime.event({'type':'thinking_delta','content':'Reasoning snapshot'})
    runtime.event({'type':'tool_output_delta','tool_call_id':'a','content':'first line\n'})
    runtime.event({'type':'tool_output_delta','tool_call_id':'a','content':'last line\n'})
    runtime.event({'type':'assistant_delta','content':'Partial reply'})
    events = client.get(f'/api/agent/sessions/{session_id}/journal').json()['events']
    assert any(event['type'] == 'assistant_completed' for event in events)
    assert any(event['type'] == 'thinking_delta' and event['content']=='Reasoning snapshot' for event in events)
    assert [event['content'] for event in events if event['type']=='tool_output_delta'] == ['first line\n','last line\n']
    assert any(event['type']=='assistant_delta' and event['content']=='Partial reply' for event in events)
    for event in events:
        assert datetime.fromisoformat(event['occurred_at']).tzinfo is not None
    assert client.get(f'/api/agent/sessions/{session_id}/journal').json()['events']==events


def test_configuration_snapshot_and_runtime_model_switch(client):
    session_id = new_session(client)
    ws = d.get('workspace', 'writing')
    ws['system_prompt'] = 'New prompt'
    assert client.put('/api/workspaces/writing', json=ws).status_code == 200
    assert d.get('session', session_id)['config']['system_prompt'] != 'New prompt'
    response = client.put(f'/api/agent/sessions/{session_id}/model', json={'workspace_id': 'writing', 'model_id': 'test/model', 'reasoning_effort': 'high'})
    assert response.status_code == 200
    assert response.json()['reasoning_effort'] == 'high'
    assert client.put(f'/api/agent/sessions/{session_id}/model', json={'workspace_id': 'writing', 'model_id': 'test/model', 'reasoning_effort': 'invalid'}).status_code == 422
    assert client.post(f'/api/agent/sessions/{session_id}/actions/approve', json={'decisions': []}).status_code == 409
    response = client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'hello'})
    assert response.status_code == 202
    assert client.get('/api/agent/sessions/' + session_id).json()['status'] == 'completed'
    response = client.put(f'/api/agent/sessions/{session_id}/model', json={'workspace_id': 'writing', 'model_id': 'test/model', 'reasoning_effort': 'low'})
    assert response.status_code == 200, response.text
    assert rt.RUNTIMES[session_id].runner.reasoning_effort() == 'low'
    response = client.put(f'/api/agent/sessions/{session_id}/permission', json={'mode': 'allow', 'confirmation': 'ALLOW'})
    assert response.status_code == 200, response.text
    assert rt.RUNTIMES[session_id].runner.permission_mode().value == 'allow'
    assert client.put(f'/api/agent/sessions/{session_id}/permission', json={'mode': 'ask'}).status_code == 200
    assert 'secret-adapter-test' not in client.get('/api/agent/sessions').text


def test_permission_before_first_message_and_full_access_default(client, tmp_path):
    assert client.post('/api/agent/sessions', json={'workspace_id': 'writing', 'permission_mode': 'allow'}).status_code == 422
    session_id = new_session(client)
    base = f'/api/agent/sessions/{session_id}'
    record = client.get(base).json()
    assert record['sandbox_mode'] == 'full-access'
    assert record['permission_mode'] == 'ask'
    assert client.put(base + '/permission', json={'mode': 'allow'}).status_code == 422
    assert client.put(base + '/permission', json={'mode': 'allow', 'confirmation': 'ALLOW'}).status_code == 200
    assert client.post(base + '/messages', json={'text': 'write a file'}).status_code == 202
    record = client.get(base).json()
    assert record['status'] == 'completed', record
    assert (tmp_path / 'workspaces/writing/files/hello.txt').exists()
    runner = rt.RUNTIMES[session_id].runner
    assert runner.permission_mode().value == 'allow'
    assert runner.sandbox_mode() == 'full-access'
    # A file outside the workspace is visible through the actual full-access backend.
    external = tmp_path / 'host-visible.txt'
    external.write_text('full-access-ok')
    result = runner.prepared.backend.download_files([str(external)])
    assert result[0].content == b'full-access-ok'
    # Platform credentials retain the native protected-file boundary.
    protected = runner.prepared.backend.download_files([str(tmp_path / 'model-config.key')])
    assert protected[0].error
    assert client.put(base + '/permission', json={'mode': 'ask'}).json()['permission_mode'] == 'ask'


def test_permission_waiting_interaction_and_pending_boundary(client, monkeypatch):
    session_id = new_session(client)
    base = f'/api/agent/sessions/{session_id}'
    client.post(base + '/messages', json={'text': 'write a file'})
    assert client.put(base + '/permission', json={'mode': 'allow', 'confirmation': 'ALLOW'}).status_code == 409
    assert approve(client, session_id).status_code == 202
    runner = rt.RUNTIMES[session_id].runner
    record = d.get('session', session_id)
    record['status'] = 'running'
    d.put('session', record)
    response = client.put(base + '/permission', json={'mode': 'allow', 'confirmation': 'ALLOW'})
    assert response.status_code == 200
    assert response.json()['permission_mode'] == 'ask'
    assert response.json()['pending_permission_mode'] == 'allow'
    runner.apply_pending_config_if_idle()
    response = client.get(base).json()
    assert response['permission_mode'] == 'allow'
    assert response['pending_permission_mode'] is None


def test_core_tool_validation(client):
    config = d.get('workspace', 'writing')
    config['core_tools'] = {'unknown_tool': True}
    assert client.put('/api/workspaces/writing', json=config).status_code == 422
    assert client.post('/api/agent/sessions', json={'workspace_id': 'missing'}).status_code == 404
    assert client.post('/api/agent/sessions/missing/messages', json={'text': 'hello'}).status_code == 404


def test_native_human_input_and_event_resume(client):
    session_id = new_session(client)
    assert client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'ask me a question'}).status_code == 202
    record = client.get('/api/agent/sessions/' + session_id).json()
    assert record['status'] == 'waiting_human', record
    assert record['human_input']['fields'][0]['id'] == 'title'
    assert client.post(f'/api/agent/sessions/{session_id}/actions/answer', json={'values': {}}).status_code == 422
    assert client.post(f'/api/agent/sessions/{session_id}/actions/compact', json={}).status_code == 409
    assert client.post(f'/api/agent/sessions/{session_id}/actions/answer', json={'values': {'title': 'A real answer'}}).status_code == 202
    assert client.get('/api/agent/sessions/' + session_id).json()['status'] == 'completed'
    first = rt.journal(session_id, 0)
    assert first
    pivot = first[len(first) // 2]['seq']
    rest = rt.journal(session_id, pivot)
    assert rest and all(e['seq'] > pivot for e in rest)
    assert rt.journal('different-session', 0) == []


def test_mcp_config_uses_native_validator(client):
    config = d.get('workspace', 'writing')
    config['mcp'] = {'enabled': False, 'servers': {'docs': {'transport': 'streamable_http', 'url': 'https://example.com/mcp', 'auth': {'type': 'bearer', 'token_env': 'TEST_MCP_TOKEN'}}}}
    assert client.put('/api/workspaces/writing', json=config).status_code == 200
    config = d.get('workspace', 'writing')
    config['mcp']['servers']['docs']['api_key'] = 'must-use-env-references'
    assert client.put('/api/workspaces/writing', json=config).status_code == 422


def test_file_upload_is_workspace_scoped_and_reaches_real_core(client):
    response = client.post('/api/agent/workspaces/writing/uploads', files={'file': ('notes.txt', b'uploaded-content-read-by-native-tool', 'text/plain')})
    assert response.status_code == 201, response.text
    file = response.json()
    assert client.get('/api/agent/workspaces/writing/files/' + file['path']).content == b'uploaded-content-read-by-native-tool'
    assert client.post('/api/agent/workspaces/missing/uploads', files={'file': ('x.txt', b'x')}).status_code == 404
    assert client.post('/api/agent/workspaces/writing/uploads', files={'file': ('empty.txt', b'')}).status_code == 422
    session_id = new_session(client)
    url = f'/api/agent/sessions/{session_id}/messages'
    assert client.post(url, json={'text': 'inspect uploaded file', 'upload_ids': [file['id']]}).status_code == 202
    record = client.get('/api/agent/sessions/' + session_id).json()
    assert record['messages'][0]['attachments'][0]['name'] == 'notes.txt'
    assert approve(client, session_id).status_code == 202
    assert client.get('/api/agent/sessions/' + session_id).json()['status'] == 'completed'
    assert any(e['type'] == 'tool_completed' and 'uploaded-content-read-by-native-tool' in e.get('content', '') for e in d.all_records('event'))
    assert client.post(url, json={'text': 'x', 'upload_ids': [file['id'], file['id']]}).status_code == 422
    other = client.post('/api/agent/sessions', json={'workspace_id': 'office', 'model_id': 'test/model'}).json()
    assert client.post(f'/api/agent/sessions/{other["id"]}/messages', json={'text': 'x', 'upload_ids': [file['id']]}).status_code == 404


def test_workspace_file_download_and_guards(client, tmp_path):
    root = d.DATA / 'workspaces' / 'writing' / 'files'
    root.mkdir(parents=True, exist_ok=True)
    (root / 'nested').mkdir()
    payload = b'# Hello'
    (root / 'nested' / 'notes.md').write_bytes(payload)
    response = client.get('/api/agent/workspaces/writing/files/nested/notes.md')
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers['content-disposition'].startswith('attachment;')
    assert client.get('/api/agent/workspaces/writing/files').json() == [
        {'path': 'nested/notes.md', 'size': len(payload)}]
    outside = tmp_path / 'outside.md'
    outside.write_text('private')
    (root / 'link.md').symlink_to(outside)
    assert client.get('/api/agent/workspaces/writing/files/link.md').status_code == 404
    assert client.get('/api/agent/workspaces/writing/files/%2E%2E/platform.sqlite').status_code == 404
    assert client.get('/api/agent/workspaces/missing/files/notes.md').status_code == 404


def test_file_preview_and_edit_endpoints_removed(client):
    root = d.DATA / 'workspaces' / 'writing' / 'files'
    root.mkdir(parents=True, exist_ok=True)
    notes = root / 'notes.md'
    notes.write_text('Original')
    assert client.get('/api/agent/workspaces/writing/file', params={'path': 'notes.md'}).status_code == 404
    assert client.get('/api/agent/workspaces/writing/file-view', params={'path': 'notes.md'}).status_code == 404
    assert client.put('/api/agent/workspaces/writing/file', json={'path': 'notes.md', 'content': 'Changed'}).status_code == 404
    assert client.put('/api/agent/workspaces/writing/document', params={'path': 'notes.md'},
        files={'file': ('notes.md', b'Changed')}, data={'revision': 'old'}).status_code == 404
    assert notes.read_text() == 'Original'


@pytest.mark.parametrize('kind,base', [('session', '/api/agent/sessions'), ('task', '/api/tasks')])
def test_artifact_download_preserves_source(client, kind, base):
    d.put(kind, {'id': 'artifact-download', 'workspace_id': 'writing'})
    root = d.DATA / 'artifacts' / 'writing' / 'artifact-download'
    root.mkdir(parents=True)
    (root / 'notes.md').write_text('Artifact original')
    response = client.get(f'{base}/artifact-download/artifacts/notes.md')
    assert response.status_code == 200
    assert response.content == b'Artifact original'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert not (d.DATA / 'workspaces' / 'writing' / 'files' / 'notes.md').exists()


def test_upload_size_limit_and_sanitized_filename(client, monkeypatch):
    monkeypatch.setattr(rt, 'MAX_UPLOAD_BYTES', 10)
    assert client.post('/api/agent/workspaces/writing/uploads', files={'file': ('big.txt', b'x' * 11)}).status_code == 413
    response = client.post('/api/agent/workspaces/writing/uploads', files={'file': ('../../notes.txt', b'hello')})
    assert response.status_code == 201
    assert response.json()['name'] == 'notes.txt'
    assert response.json()['path'].startswith('uploads/')


def test_real_core_cache_usage_and_historical_checkpoint_backfill(client):
    session_id = new_session(client)
    assert client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'cached answer'}).status_code == 202
    record = client.get('/api/agent/sessions/' + session_id).json()
    assert record['usage']['input_tokens'] == 1000
    assert record['usage']['cache_read_input_tokens'] == 750
    assert record['usage']['cache_hit_rate'] == 0.75
    # Existing records stored only top-level token counts; recover actual details
    # from the native delta checkpoint without making a model request.
    saved = d.get('session', session_id)
    saved['usage'].pop('cache_read_input_tokens')
    saved['usage'].pop('cache_hit_rate')
    d.put('session', saved)
    rt.initialize()
    assert client.get('/api/agent/sessions/' + session_id).json()['usage']['cache_hit_rate'] == 0.75
    # A later request without cache fields must not inherit the prior hit rate.
    client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'hello'})
    assert client.get('/api/agent/sessions/' + session_id).json()['usage']['cache_hit_rate'] is None


@pytest.mark.parametrize('entrypoint', ['session', 'legacy'])
def test_native_subagent_executes_and_returns_to_parent(client, monkeypatch, entrypoint):
    if entrypoint == 'session':
        target = d.DATA / 'workspaces' / 'writing' / 'files' / 'delegated.txt'
        def build_model(profile, **kwargs):
            model = DelegationModel(target=str(target))
            object.__setattr__(model, '_deep_agent_model_id', profile.id)
            return model
        monkeypatch.setattr('agent.llm.build_chat_model', build_model)
        monkeypatch.setattr('agent.runner.build_chat_model', build_model)
        session_id = new_session(client)
        assert client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'Delegate an independent file task.'}).status_code == 202
        record = client.get('/api/agent/sessions/' + session_id).json()
        assert record['status'] == 'completed', record
        assert rt.RUNTIMES[session_id].runner.settings.subagents.enabled
        assert any(e['type'] == 'subagents_changed' and e['result'].get('count') == 1 for e in d.all_records('event'))
    else:
        from platform_app.main import run_task
        target = d.DATA / 'artifacts' / 'writing' / 'legacy-delegation' / 'delegated.txt'
        record = dict(id='legacy-delegation', workspace_id='writing', prompt='Delegate an independent file task.', config=d.get('workspace', 'writing'), status='queued', output='', error=None, revision=1)
        run_task(record, model=DelegationModel(target=str(target)), raise_errors=True)
        record = d.get('task', record['id'])
        assert record['status'] == 'completed', record
    assert target.read_text() == 'delegated-ok'
    assert record['output'] == 'Main agent received subagent result.'


def test_everything_aggregates_resources_and_freezes_session_snapshot(client, monkeypatch, tmp_path):
    import agent.config
    cli = tmp_path / 'cli-skills' / 'example'
    cli.mkdir(parents=True)
    (cli / 'SKILL.md').write_text('---\nname: cli-example\ndescription: CLI skill example\n---\nInstructions')
    monkeypatch.setattr(agent.config, 'default_skills_dir', lambda: cli.parent)
    monkeypatch.setattr('agent.sandbox.default_skills_dir', lambda: cli.parent)
    everything = next(w for w in client.get('/api/workspaces').json() if w['id'] == 'everything')
    assert everything['bind_all']
    assert all(everything['core_tools'].values())
    assert {t['name'] for t in everything['tools']} == set(d.TOOL_SCHEMAS)
    assert any(s['name'] == '办公文档' for s in everything['skills'])
    assert any(s['name'] == 'cli-example' for s in everything['native_skills'])
    session = client.post('/api/agent/sessions', json={'workspace_id': 'everything', 'model_id': 'test/model'}).json()
    snapshot = d.get('session', session['id'])['config']
    office = d.get('workspace', 'office')
    office['skills'].append({'name': 'New skill', 'content': 'A new workflow'})
    assert client.put('/api/workspaces/office', json=office).status_code == 200
    updated = next(w for w in client.get('/api/workspaces').json() if w['id'] == 'everything')
    assert any(s['name'] == 'New skill' for s in updated['skills'])
    assert all(s['name'] != 'New skill' for s in snapshot['skills'])
    # Saving metadata does not copy derived skills into Everything's own record.
    assert client.put('/api/workspaces/everything', json=updated).status_code == 200
    assert d.get('workspace', 'everything')['skills'] == []
    monkeypatch.setattr(CoreModel, 'bind_tools', lambda self, *args, **kwargs: self)
    assert client.post(f'/api/agent/sessions/{session["id"]}/messages', json={'text': 'hello'}).status_code == 202
    assert client.get('/api/agent/sessions/' + session['id']).json()['status'] == 'completed'
    runner = rt.RUNTIMES[session['id']].runner
    assert '/workspace-skills/installed/' in runner.prepared.skill_sources
    assert runner.prepared.backend.download_files(['/workspace-skills/installed/example/SKILL.md'])[0].content.endswith(b'Instructions')


def test_deepseek_tools_replace_tavily_and_follow_permissions(client, monkeypatch):
    # The native create_agent would register Tavily with this variable set.
    monkeypatch.setenv('TAVILY_API_KEY', 'must-never-be-used')
    client.post('/api/agent/sessions/' + new_session(client) + '/messages', json={'text': 'hello'})
    runner = next(runtime.runner for runtime in rt.RUNTIMES.values() if runtime.runner)
    assert runner.prepared.spec.web_search_api_key is None
    tools = runner.prepared.graph.nodes['tools'].bound.tools_by_name
    assert tools['web_search'].func.__module__ == 'platform_app.web_tools'
    assert tools['web_fetch'].func.__module__ == 'platform_app.web_tools'
    assert 'web_fetch' in runner.prepared.interrupt_on
    runner.request_permission_change('allow')
    runner.apply_pending_config_if_idle()
    assert not runner.prepared.interrupt_on
    assert runner.prepared.spec.web_search_api_key is None
    runner.request_permission_change('ask')
    runner.apply_pending_config_if_idle()
    assert 'web_fetch' in runner.prepared.interrupt_on
    ws = d.get('workspace', 'writing')
    ws['core_tools'].update(web_search=False, web_fetch=False)
    d.put('workspace', ws)
    session = new_session(client)
    client.post(f'/api/agent/sessions/{session}/messages', json={'text': 'hello'})
    hidden = rt.RUNTIMES[session].runner.prepared.model_tool_names
    assert 'web_search' not in hidden and 'web_fetch' not in hidden
    assert 'task' in hidden


def test_installed_skill_bindings_reach_native_discovery_and_freeze_resources(client, monkeypatch, tmp_path):
    import agent.config
    root = tmp_path / 'installed-skills'
    for name in ('docx-author', 'other-skill'):
        path = root / name
        (path / 'scripts').mkdir(parents=True)
        (path / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: {name} description\n---\nOriginal instructions')
        (path / 'scripts' / 'helper.py').write_text('print("original helper")')
    monkeypatch.setattr(agent.config, 'default_skills_dir', lambda: root)
    monkeypatch.setattr('agent.sandbox.default_skills_dir', lambda: root)
    workspaces = client.get('/api/workspaces').json()
    writing = next(w for w in workspaces if w['id'] == 'writing')
    assert {s['id'] for s in writing['native_skills']} == {'docx-author', 'other-skill'}
    assert all(not s['enabled'] for s in writing['native_skills'])
    writing['skill_bindings'] = {'docx-author': True, 'other-skill': False}
    saved = client.put('/api/workspaces/writing', json=writing)
    assert saved.status_code == 200, saved.text
    assert [s['id'] for s in saved.json()['native_skills'] if s['enabled']] == ['docx-author']
    session = new_session(client)
    assert client.post(f'/api/agent/sessions/{session}/messages', json={'text': 'hello'}).status_code == 202
    runner = rt.RUNTIMES[session].runner
    files = runner.prepared.backend.download_files(['/workspace-skills/installed/docx-author/scripts/helper.py'])
    assert b'original helper' in files[0].content
    snapshot = tmp_path / 'skill-snapshots' / session / 'installed'
    assert not (snapshot / 'other-skill').exists()
    assert runner.prepared.backend.write('/workspace-skills/installed/docx-author/scripts/helper.py', 'changed').error
    # A new workspace selection does not modify an existing session's snapshot.
    writing = saved.json()
    writing['skill_bindings']['docx-author'] = False
    assert client.put('/api/workspaces/writing', json=writing).status_code == 200
    (root / 'docx-author' / 'scripts' / 'helper.py').write_text('print("new helper")')
    assert b'original helper' in runner.prepared.backend.download_files(['/workspace-skills/installed/docx-author/scripts/helper.py'])[0].content
    assert d.get('session', session)['config']['skill_bindings']['docx-author']
    everything = next(w for w in client.get('/api/workspaces').json() if w['id'] == 'everything')
    assert all(s['enabled'] for s in everything['native_skills'])


def test_skill_binding_validation(client):
    workspace = next(w for w in client.get('/api/workspaces').json() if w['id'] == 'writing')
    for invalid in ({'../escape': True}, {'not-installed': True}, {'fake': 'yes'}):
        workspace['skill_bindings'] = invalid
        assert client.put('/api/workspaces/writing', json=workspace).status_code == 422


def test_docx_revisions_are_journaled_during_real_runner_invocation(client, monkeypatch):
    """Every next model call can already read the previous commit in the journal."""
    import json
    from platform_app.documents import DocumentStore

    class StreamingDocxModel(BaseChatModel):
        @property
        def _llm_type(self): return 'streaming-docx-test'
        def bind_tools(self, tools, **kwargs):
            names = [t.get('name') if isinstance(t, dict) else t.name for t in tools]
            assert 'create_docx' not in names and 'docx_apply_one' in names
            return self
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            completed = [m for m in messages if isinstance(m, ToolMessage)]
            root = d.DATA / 'workspaces' / 'writing' / 'files'
            if not completed:
                name, args = 'docx_init', {'document_id': 'report'}
            else:
                state = DocumentStore(root / 'report.docx').status()
                revisions = [e for e in d.all_records('event') if e['type'] in ('document_initialized','document_revision')]
                assert len(revisions) >= state['revision'] + 1
                if state['revision'] >= 10:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content='Draft ready; visual review pending.'))])
                if state['gate'] != 'open':
                    name, args = 'docx_checkpoint', {'document_id': 'report'}
                else:
                    n = state['revision'] + 1
                    name = 'docx_apply_one'
                    args = dict(document_id='report', operation_id=f'op-{n}', base_revision=n-1,
                        operation=dict(type='append_paragraph', block_id=f'p-{n}', text=f'Paragraph {n}'))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[dict(name=name, args=args, id=f'doc-{len(completed)}', type='tool_call')]))])

    # Deterministic rendering fixture; live main-Agent/tool execution is real.
    import pymupdf as fitz
    from platform_app.documents import engine
    def render(docx, output, dpi=125):
        doc = fitz.open(); page = doc.new_page(); page.insert_text((72,72), 'Paragraph body')
        doc.save(output / 'preview.pdf'); page.get_pixmap().save(output / 'page-001.png'); doc.close()
    monkeypatch.setattr(engine.o, 'run_render', render)
    monkeypatch.setattr('agent.llm.build_chat_model', lambda *args, **kwargs: StreamingDocxModel())
    monkeypatch.setattr('agent.runner.build_chat_model', lambda *args, **kwargs: StreamingDocxModel())
    session_id = new_session(client)
    response = client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'Write a report incrementally'})
    assert response.status_code == 202, response.text
    assert client.get(f'/api/agent/sessions/{session_id}').json()['status'] == 'completed'
    journal = client.get(f'/api/agent/sessions/{session_id}/journal').json()['events']
    updates = [e for e in journal if e['type'] in ('document_initialized', 'document_revision')]
    assert [e['revision'] for e in updates] == list(range(11))
    assert updates[-1]['seq'] < next(e['seq'] for e in journal if e['type'] == 'result')
    docs = client.get(f'/api/agent/sessions/{session_id}/documents').json()
    assert docs[0]['revision'] == 10
    assert client.get(f'/api/agent/sessions/{session_id}/documents/report/revisions/2').content.startswith(b'PK')
    # Rebuild runtime without losing latest version or replaying old store events.
    runtime = rt.RUNTIMES[session_id]
    runtime.artifact_services['docx'].close(); runtime.runner.close(); runtime.runner.session_store.close(); rt.RUNTIMES.clear()
    rt.initialize()
    assert client.get(f'/api/agent/sessions/{session_id}/documents').json()[0]['revision'] == 10
    rebuilt = rt.runtime(session_id); rebuilt.build(); rebuilt.artifact_services['docx'].sync()
    assert len([e for e in d.all_records('event') if e['type'] in ('document_initialized','document_revision')]) == 11
    # Internal version database is never downloadable/listed as a workspace file.
    assert all('.docx-state' not in item['path'] for item in client.get('/api/agent/workspaces/writing/files').json())
    assert client.get('/api/agent/workspaces/writing/files/.docx-state/report.docx/history.sqlite').status_code == 404


def test_docx_external_tool_boundary_tracking_and_visual_capability(client):
    from platform_app.documents import Documents, DocumentStore
    from dataclasses import dataclass
    session_id = new_session(client)
    runtime = rt.runtime(session_id)
    root = d.DATA / 'workspaces' / 'writing' / 'files'; root.mkdir(parents=True, exist_ok=True)
    runtime.artifact_services['docx'] = Documents(root, 'writing', lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))))
    runtime.artifact_services['docx'].execute('init', {'document_id': 'report'})
    runtime.artifact_services['docx'].execute('apply_one', dict(document_id='report', base_revision=0, operation_id='p1',
        operation=dict(type='append_paragraph', block_id='p1', text='Original')))
    from docx import Document
    doc = Document(root / 'report.docx'); doc.add_paragraph('Native tool wrote this'); doc.save(root / 'report.docx')
    @dataclass
    class Event:
        type: str = 'tool_completed'
        name: str = 'execute'
    runtime.core_event(Event())
    store = DocumentStore(root / 'report.docx')
    assert store.status()['revision'] == 2
    assert store.status()['gate'] == 'checkpoint'
    with pytest.raises(ValueError, match='Text-only'):
        runtime.artifact_services['docx'].execute('review', dict(document_id='report', revision=2, page=1, passed=True, notes='Cannot see screenshot'))
    assert client.get(f'/api/agent/sessions/{session_id}/documents').json()[0]['revision'] == 2
    other = new_session(client)
    assert client.get(f'/api/agent/sessions/{other}/documents/report/revisions/2').status_code == 404


def test_docx_steering_persists_viewed_revision_and_uses_current_version(client):
    from platform_app.documents import Documents
    from unittest.mock import Mock
    session_id = new_session(client); runtime = rt.runtime(session_id)
    root = d.DATA / 'workspaces' / 'writing' / 'files'
    runtime.artifact_services['docx'] = Documents(root, 'writing', lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))))
    runtime.artifact_services['docx'].execute('init', {'document_id': 'report'})
    runtime.artifact_services['docx'].execute('apply_one', dict(document_id='report', base_revision=0, operation_id='p1',
        operation=dict(type='append_paragraph', block_id='p1', text='Original')))
    record = d.get('session', session_id); record['status'] = 'running'; d.put('session', record)
    runtime.runner = Mock()
    result = client.post(f'/api/agent/sessions/{session_id}/actions/steer', json=dict(text='Use business language', artifact=dict(feature='docx',document_id='report', revision=0)))
    assert result.status_code == 202
    instruction = runtime.runner.steer.call_args.args[0]
    assert 'viewed_revision=0' in instruction and 'current_revision=1' in instruction
    event = [e for e in rt.journal(session_id, 0) if e['type'] == 'steer'][-1]
    assert event['document']['revision'] == 0 and event['status'] == 'accepted_pending_boundary'
    assert client.post(f'/api/agent/sessions/{session_id}/actions/steer', json=dict(text='bad', artifact=dict(feature='docx',document_id='report', revision=999))).status_code == 409
    runtime.runner = None


def test_nested_native_docx_is_tracked_and_downloadable(client):
    from platform_app.documents import Documents
    from docx import Document
    session_id=new_session(client); runtime=rt.runtime(session_id)
    root=d.DATA/'workspaces'/'writing'/'files'; (root/'reports').mkdir(parents=True)
    document=Document(); document.add_paragraph('Native nested report'); document.save(root/'reports'/'report.docx')
    runtime.artifact_services['docx']=Documents(root,'writing',lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))))
    runtime.artifact_services['docx'].detect_external()
    result=client.get(f'/api/agent/sessions/{session_id}/documents').json()
    assert result[0]['path']=='reports/report.docx' and result[0]['revision']==0
    assert client.get(f'/api/agent/sessions/{session_id}/documents/{result[0]["document_id"]}/revisions/0').content.startswith(b'PK')


def test_background_preview_follows_the_last_commit(client, monkeypatch):
    import threading
    import time
    import pymupdf as fitz
    from platform_app.documents import Documents, engine
    started=threading.Event(); release=threading.Event()
    def render(docx, output, dpi=125):
        started.set(); assert release.wait(5)
        doc=fitz.open(); page=doc.new_page(); page.insert_text((72,72),'Actual one-page render fixture')
        doc.save(output/'preview.pdf'); page.get_pixmap().save(output/'page-001.png'); doc.close()
    monkeypatch.setattr(engine.o,'run_render',render)
    session_id=new_session(client); runtime=rt.runtime(session_id)
    root=d.DATA/'workspaces'/'writing'/'files'
    runtime.artifact_services['docx']=Documents(root,'writing',lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))),live_preview=True)
    docs=runtime.artifact_services['docx']
    docs.execute('init',dict(document_id='report'))
    assert started.wait(5)
    for i in range(3):
        docs.execute('apply_one',dict(document_id='report',base_revision=i,operation_id=f'op-{i}',operation=dict(type='append_paragraph',block_id=f'p-{i}',text=f'Short formatted fixture {i}')))
    release.set()
    end=time.time()+5
    while time.time()<end:
        state=engine.DocumentStore(root/'report.docx').status()
        delivered=[event.get('preview_revision') for event in rt.journal(session_id,0) if event['type']=='document_preview']
        if state.get('preview_revision')==3 and delivered and delivered[-1]==3:break
        time.sleep(.01)
    assert state.get('preview_revision')==3
    previews=[event['preview_revision'] for event in rt.journal(session_id,0) if event['type']=='document_preview']
    assert previews==sorted(set(previews)) and previews[-1]==3


def test_invalid_native_write_keeps_last_committed_document_visible(client):
    from platform_app.documents import Documents
    session_id=new_session(client); runtime=rt.runtime(session_id)
    root=d.DATA/'workspaces'/'writing'/'files'
    runtime.artifact_services['docx']=Documents(root,'writing',lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))))
    runtime.artifact_services['docx'].execute('init',dict(document_id='report'))
    original=(root/'report.docx').read_bytes()
    (root/'report.docx').write_bytes(b'partial invalid native ZIP')
    runtime.artifact_services['docx'].detect_external()
    docs=client.get(f'/api/agent/sessions/{session_id}/documents').json()
    assert docs[0]['revision']==0 and docs[0]['external_conflict'] is True
    assert client.get(f'/api/agent/sessions/{session_id}/documents/report/revisions/0').content==original


def test_agent_runs_with_both_format_features_uninstalled(client):
    assert client.delete('/api/features/docx').status_code==200
    assert client.delete('/api/features/pptx').status_code==200
    session_id=new_session(client)
    response=client.post(f'/api/agent/sessions/{session_id}/messages',json={'text':'hello without document extensions'})
    assert response.status_code==202
    assert client.get(f'/api/agent/sessions/{session_id}').json()['status']=='completed'
    native=rt.RUNTIMES[session_id].runner
    assert not any(name.startswith(('docx_','pptx_')) for name in native.prepared.model_tool_names)
    assert 'execute' in native.prepared.model_tool_names
    assert rt.RUNTIMES[session_id].artifact_services=={}


def test_native_conversation_storage_is_not_an_output_or_download(client):
    root=d.DATA/'workspaces'/'writing'/'files';internal=root/'.deepagents'/'conversation_history'
    internal.mkdir(parents=True);(internal/'session_private.md').write_text('internal trace')
    (root/'report.txt').write_text('public output')
    assert rt.changed_workspace_files(root,{})==[{'path':'report.txt','size':13}]
    assert client.get('/api/agent/workspaces/writing/files/.deepagents/conversation_history/session_private.md').status_code==404
    assert client.get('/api/agent/workspaces/writing/files').json()==[{'path':'report.txt','size':13}]
    event=rt.public_event(dict(type='output_files',files=[dict(path='.deepagents/conversation_history/session_private.md'),dict(path='report.txt')]))
    assert event['files']==[dict(path='report.txt')]
