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
    assert any(e['type'] == 'tool_completed' and 'core-command-ok' in e.get('content', '') for e in journal)
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


def test_completed_session_journal_omits_token_deltas(client):
    session_id = new_session(client)
    assert client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'cached answer'}).status_code == 202
    record = d.get('session', session_id)
    seq = record['event_seq'] + 1
    d.put('event', {'id': f'{session_id}:{seq:010d}', 'session_id': session_id, 'type': 'thinking_delta', 'content': 'partial', 'seq': seq})
    record['event_seq'] = seq
    d.put('session', record)
    events = client.get(f'/api/agent/sessions/{session_id}/journal').json()['events']
    assert any(event['type'] == 'assistant_completed' for event in events)
    assert not any(event['type'] in ('thinking_delta', 'assistant_delta', 'tool_output_delta') for event in events)


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


def test_workspace_text_file_preview_edit_and_guards(client):
    root = d.DATA / 'workspaces' / 'writing' / 'files'
    root.mkdir(parents=True, exist_ok=True)
    notes = root / 'notes.md'
    notes.write_text('# Hello', encoding='utf-8')
    preview = client.get('/api/agent/workspaces/writing/file', params={'path': 'notes.md'}).json()
    assert preview == {'path': 'notes.md', 'size': 7, 'editable': True, 'content': '# Hello', 'reason': ''}
    saved = client.put('/api/agent/workspaces/writing/file', json={'path': 'notes.md', 'content': '# Updated\n'})
    assert saved.status_code == 200, saved.text
    assert notes.read_text() == '# Updated\n'
    assert saved.json()['size'] == 10
    binary = root / 'report.docx'
    binary.write_bytes(b'PK\x03\x04\x00\x00data')
    assert client.get('/api/agent/workspaces/writing/file', params={'path': 'report.docx'}).json()['editable'] is False
    assert client.put('/api/agent/workspaces/writing/file', json={'path': 'report.docx', 'content': 'x'}).status_code == 422
    assert client.get('/api/agent/workspaces/writing/file', params={'path': '../platform.sqlite'}).status_code == 404
    assert client.put('/api/agent/workspaces/writing/file', json={'path': '../escape.md', 'content': 'x'}).status_code == 404


def test_docx_editor_roundtrip_and_save_guards(client, monkeypatch, tmp_path):
    import io
    from docx import Document

    def document_bytes(text):
        doc = Document()
        doc.add_paragraph(text)
        data = io.BytesIO()
        doc.save(data)
        return data.getvalue()

    root = d.DATA / 'workspaces' / 'writing' / 'files'
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'report.docx'
    original = document_bytes('Original')
    path.write_bytes(original)
    preview = client.get('/api/agent/workspaces/writing/file', params={'path': 'report.docx'}).json()
    assert preview['format'] == 'docx'
    revision = preview['revision']

    def save(data, name='report.docx', version=revision):
        return client.put('/api/agent/workspaces/writing/document', params={'path': name},
            files={'file': ('edited.docx', data, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')},
            data={'revision': version})

    assert save(b'not a document').status_code == 422
    assert path.read_bytes() == original
    edited = document_bytes('Edited in browser')
    result = save(edited)
    assert result.status_code == 200, result.text
    assert result.json()['revision'] != revision
    downloaded = client.get('/api/agent/workspaces/writing/files/report.docx')
    assert Document(io.BytesIO(downloaded.content)).paragraphs[0].text == 'Edited in browser'
    assert save(original).status_code == 409
    assert path.read_bytes() == edited
    assert save(edited, '../escape.docx').status_code == 404
    (root / 'notes.txt').write_text('text')
    assert save(edited, 'notes.txt').status_code == 422
    outside = tmp_path / 'outside.docx'
    outside.write_bytes(original)
    (root / 'link.docx').symlink_to(outside)
    assert save(edited, 'link.docx').status_code == 404
    monkeypatch.setattr(rt, 'MAX_UPLOAD_BYTES', 10)
    assert save(edited, version=result.json()['revision']).status_code == 413
    assert path.read_bytes() == edited


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
