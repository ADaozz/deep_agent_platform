"""Exercise the HTTP adapter against the real core and real Bubblewrap tools."""

import pytest
import shlex
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from platform_app import domain as d, models as m, runtime as rt, format_plugins
from platform_app.main import app


def current_id():
    sessions = d.all_records('session')
    return sessions[-1]['id'] if sessions else rt.create_session(rt.NewSession(model_id='test/model'))['id']


def files_root():
    return d.files_root(current_id())


def file_api():
    return '/api/agent/sessions/' + current_id() + '/files'


def upload_api():
    return '/api/agent/sessions/' + current_id() + '/uploads'


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
        assert 'read_file' not in names  # global setting reaches native model tools
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        last_user = max(i for i, message in enumerate(messages) if isinstance(message, HumanMessage))
        text = messages[last_user].content
        if text == 'write a file' and not any(isinstance(message, ToolMessage) for message in messages[last_user:]):
            msg = AIMessage(content='', tool_calls=[{'name': 'write_file', 'args': {'file_path': self.target, 'content': 'from the real kernel'}, 'id': 'write-1', 'type': 'tool_call'}])
        elif text == 'run a command' and not any(isinstance(message, ToolMessage) for message in messages[last_user:]):
            msg = AIMessage(content='', tool_calls=[{'name': 'execute', 'args': {'command': 'printf core-command-ok'}, 'id': 'exec-1', 'type': 'tool_call'}])
        elif text.startswith('network command: ') and not any(isinstance(message, ToolMessage) for message in messages[last_user:]):
            msg = AIMessage(content='', tool_calls=[{'name': 'execute', 'args': {'command': text.removeprefix('network command: '), 'network': True}, 'id': 'network-1', 'type': 'tool_call'}])
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
    ws = d.get('settings', 'global')
    ws['core_tools'] = {'read_file': False}
    ws['skills'] = [{'name': 'Test skill', 'description': 'Test reusable workflow', 'content': 'Use precise output.', 'enabled': True}]
    d.put('settings', ws)
    def build_model(profile, **kwargs):
        model = CoreModel(target=str(files_root() / 'hello.txt'))
        object.__setattr__(model, '_deep_agent_model_id', profile.id)
        return model
    monkeypatch.setattr('platform_app.agent_core.llm.build_chat_model', build_model)
    monkeypatch.setattr('platform_app.agent_core.runner.build_chat_model', build_model)
    monkeypatch.setattr(rt.POOL, 'submit', lambda fn, *args: fn(*args))
    yield TestClient(app)
    for runtime in rt.RUNTIMES.values():
        for service in runtime.artifact_services.values():service.close()
        if runtime.runner:
            runtime.runner.close()
            runtime.runner.session_store.close()
    rt.RUNTIMES.clear()


def new_session(client):
    response = client.post('/api/agent/sessions', json={'model_id': 'test/model'})
    assert response.status_code == 201, response.text
    assert 'secret-adapter-test' not in response.text
    assert 'config' not in response.json()
    return response.json()['id']


def approve(client, session_id):
    record = client.get('/api/agent/sessions/' + session_id).json()
    assert record['status'] == 'waiting_confirmation', record
    decisions = [{'tool_call_id': call['toolCallId'], 'decision': 'approve'} for call in record['pending_tool_calls']]
    return client.post(f'/api/agent/sessions/{session_id}/actions/approve', json={'decisions': decisions})


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_restricted_network_command_waits_for_approval(client, decision):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'APPROVED_NETWORK_OK')
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        session = client.post('/api/agent/sessions', json={}).json()
        base = '/api/agent/sessions/' + session['id']
        command = f'curl --noproxy "*" --max-time 3 http://127.0.0.1:{server.server_port}/approved'
        assert client.post(base + '/messages', json={'text': 'network command: ' + command}).status_code == 202
        pending = client.get(base).json()
        assert pending['status'] == 'waiting_confirmation', pending
        assert pending['sandbox_mode'] == 'restricted'
        call = pending['pending_tool_calls'][0]
        assert call['args']['network'] is True
        assert requests == []
        response = client.post(base + '/actions/approve', json={'decisions': [{'tool_call_id': call['toolCallId'], 'decision': decision}]})
        assert response.status_code == 202, response.text
        assert client.get(base).json()['status'] == 'completed'
        assert requests == (['/approved'] if decision == 'approve' else [])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_native_tools_approval_checkpoint_and_skill_mount(client, tmp_path):
    session_id = new_session(client)
    response = client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'write a file'})
    assert response.status_code == 202, response.text
    target = files_root() / 'hello.txt'
    assert not target.exists()  # real HITL prevents the actual write
    assert approve(client, session_id).status_code == 202
    saved = client.get('/api/agent/sessions/' + session_id).json()
    assert saved['status'] == 'completed', saved
    assert target.read_text() == 'from the real kernel'
    outputs = [e for e in d.all_records('event') if e['type'] == 'output_files']
    assert any(file['path'] == 'hello.txt' and file['size'] == len('from the real kernel') for event in outputs for file in event['files'])
    runner = rt.RUNTIMES[session_id].runner
    skills = runner.prepared.backend.download_files(['/agent-skills/skill-1/SKILL.md'])
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
    assert client.get(file_api() + '/hello.txt').text == 'from the real kernel'
    assert client.get(file_api() + '/%2E%2E/%2E%2E/platform.sqlite').status_code == 404


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
    ws = d.get('settings', 'global')
    ws['system_prompt'] = 'New prompt'
    assert client.put('/api/settings', json=ws).status_code == 200
    assert d.get('session', session_id)['config']['system_prompt'] != 'New prompt'
    response = client.put(f'/api/agent/sessions/{session_id}/model', json={'model_id': 'test/model', 'reasoning_effort': 'high'})
    assert response.status_code == 200
    assert response.json()['reasoning_effort'] == 'high'
    assert client.put(f'/api/agent/sessions/{session_id}/model', json={'model_id': 'test/model', 'reasoning_effort': 'invalid'}).status_code == 422
    assert client.post(f'/api/agent/sessions/{session_id}/actions/approve', json={'decisions': []}).status_code == 409
    response = client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'hello'})
    assert response.status_code == 202
    assert client.get('/api/agent/sessions/' + session_id).json()['status'] == 'completed'
    response = client.put(f'/api/agent/sessions/{session_id}/model', json={'model_id': 'test/model', 'reasoning_effort': 'low'})
    assert response.status_code == 200, response.text
    assert rt.RUNTIMES[session_id].runner.reasoning_effort() == 'low'
    response = client.put(f'/api/agent/sessions/{session_id}/permission', json={'mode': 'allow', 'confirmation': 'ALLOW'})
    assert response.status_code == 200, response.text
    assert rt.RUNTIMES[session_id].runner.permission_mode().value == 'allow'
    assert client.put(f'/api/agent/sessions/{session_id}/permission', json={'mode': 'ask'}).status_code == 200
    assert 'secret-adapter-test' not in client.get('/api/agent/sessions').text


def test_permission_mode_does_not_disable_default_isolation(client, tmp_path):
    assert client.post('/api/agent/sessions', json={'permission_mode': 'allow'}).status_code == 422
    session_id = new_session(client)
    base = f'/api/agent/sessions/{session_id}'
    record = client.get(base).json()
    assert record['sandbox_mode'] == 'restricted'
    assert record['permission_mode'] == 'ask'
    assert client.put(base + '/permission', json={'mode': 'allow'}).status_code == 422
    assert client.put(base + '/permission', json={'mode': 'allow', 'confirmation': 'ALLOW'}).status_code == 200
    assert client.post(base + '/messages', json={'text': 'write a file'}).status_code == 202
    record = client.get(base).json()
    assert record['status'] == 'completed', record
    assert (files_root() / 'hello.txt').exists()
    runner = rt.RUNTIMES[session_id].runner
    assert runner.permission_mode().value == 'allow'
    assert runner.sandbox_mode() == 'restricted'
    # A file outside the file library is visible through the actual full-access backend.
    external = tmp_path / 'host-visible.txt'
    external.write_text('full-access-ok')
    result = runner.prepared.backend.download_files([str(external)])
    assert result[0].error
    response = client.put(f'/api/agent/sessions/{session_id}/sandbox', json={'mode':'full-access','confirmation':'FULL_ACCESS'})
    assert response.status_code == 200, response.text
    assert runner.prepared.backend.download_files([str(external)])[0].content == b'full-access-ok'
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
    config = d.get('settings', 'global')
    config['core_tools'] = {'unknown_tool': True}
    assert client.put('/api/settings', json=config).status_code == 422
    assert client.post('/api/agent/sessions', json={'workspace_id': 'missing'}).status_code == 422
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
    config = d.get('settings', 'global')
    config['mcp'] = {'enabled': False, 'servers': {'docs': {'transport': 'streamable_http', 'url': 'https://example.com/mcp', 'auth': {'type': 'bearer', 'token_env': 'TEST_MCP_TOKEN'}}}}
    assert client.put('/api/settings', json=config).status_code == 200
    config = d.get('settings', 'global')
    config['mcp']['servers']['docs']['api_key'] = 'must-use-env-references'
    assert client.put('/api/settings', json=config).status_code == 422


def test_shared_file_upload_reaches_real_core(client):
    response = client.post(upload_api(), files={'file': ('notes.txt', b'uploaded-content-read-by-native-tool', 'text/plain')})
    assert response.status_code == 201, response.text
    file = response.json()
    assert client.get(file_api() + '/' + file['path']).content == b'uploaded-content-read-by-native-tool'
    assert client.post('/api/agent/workspaces/missing/uploads', files={'file': ('x.txt', b'x')}).status_code == 404
    assert client.post(upload_api(), files={'file': ('empty.txt', b'')}).status_code == 422
    session_id = current_id()
    url = f'/api/agent/sessions/{session_id}/messages'
    assert client.post(url, json={'text': 'inspect uploaded file', 'upload_ids': [file['id']]}).status_code == 202
    record = client.get('/api/agent/sessions/' + session_id).json()
    assert record['messages'][0]['attachments'][0]['name'] == 'notes.txt'
    assert approve(client, session_id).status_code == 202
    assert client.get('/api/agent/sessions/' + session_id).json()['status'] == 'completed'
    assert any(e['type'] == 'tool_completed' and 'uploaded-content-read-by-native-tool' in e.get('content', '') for e in d.all_records('event'))
    assert client.post(url, json={'text': 'x', 'upload_ids': [file['id'], file['id']]}).status_code == 422
    other = client.post('/api/agent/sessions', json={'model_id': 'test/model'}).json()
    assert client.post(f'/api/agent/sessions/{other["id"]}/messages', json={'text': 'hello', 'upload_ids': [file['id']]}).status_code == 404


def test_file_library_download_and_guards(client, tmp_path):
    root = files_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / 'nested').mkdir()
    payload = b'# Hello'
    (root / 'nested' / 'notes.md').write_bytes(payload)
    response = client.get(file_api() + '/nested/notes.md')
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers['content-disposition'].startswith('attachment;')
    assert client.get(file_api()).json() == [
        {'path': 'nested/notes.md', 'size': len(payload)}]
    outside = tmp_path / 'outside.md'
    outside.write_text('private')
    (root / 'link.md').symlink_to(outside)
    assert client.get(file_api() + '/link.md').status_code == 404
    assert client.get(file_api() + '/%2E%2E/platform.sqlite').status_code == 404
    assert client.get('/api/agent/workspaces/missing/files/notes.md').status_code == 404


def test_file_preview_and_edit_endpoints_removed(client):
    root = files_root()
    root.mkdir(parents=True, exist_ok=True)
    notes = root / 'notes.md'
    notes.write_text('Original')
    assert client.get('/api/agent/file', params={'path': 'notes.md'}).status_code == 404
    assert client.get('/api/agent/file-view', params={'path': 'notes.md'}).status_code == 404
    assert client.put('/api/agent/file', json={'path': 'notes.md', 'content': 'Changed'}).status_code == 404
    assert client.put('/api/agent/document', params={'path': 'notes.md'},
        files={'file': ('notes.md', b'Changed')}, data={'revision': 'old'}).status_code == 404
    assert notes.read_text() == 'Original'


def test_artifact_download_preserves_source(client):
    base='/api/agent/sessions'
    d.put('session', {'id': 'artifact-download'})
    root = d.artifact_root('artifact-download')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'notes.md').write_text('Artifact original')
    response = client.get(f'{base}/artifact-download/artifacts/notes.md')
    assert response.status_code == 200
    assert response.content == b'Artifact original'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert not (d.files_root('artifact-download') / 'notes.md').exists()


def test_upload_size_limit_and_sanitized_filename(client, monkeypatch):
    monkeypatch.setattr(rt, 'MAX_UPLOAD_BYTES', 10)
    assert client.post(upload_api(), files={'file': ('big.txt', b'x' * 11)}).status_code == 413
    response = client.post(upload_api(), files={'file': ('../../notes.txt', b'hello')})
    assert response.status_code == 201
    assert response.json()['name'] == 'notes.txt'
    assert response.json()['path'].startswith('uploads/')


def test_real_core_cache_usage_persists_and_does_not_leak_between_calls(client):
    session_id = new_session(client)
    assert client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'cached answer'}).status_code == 202
    record = client.get('/api/agent/sessions/' + session_id).json()
    assert record['usage']['input_tokens'] == 1000
    assert record['usage']['cache_read_input_tokens'] == 750
    assert record['usage']['cache_hit_rate'] == 0.75
    rt.initialize()
    assert client.get('/api/agent/sessions/' + session_id).json()['usage']['cache_hit_rate'] == 0.75
    # A later request without cache fields must not inherit the prior hit rate.
    client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'hello'})
    assert client.get('/api/agent/sessions/' + session_id).json()['usage']['cache_hit_rate'] is None


def test_usage_remains_visible_between_streaming_requests(client):
    session_id = new_session(client)
    assert client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'cached answer'}).status_code == 202
    record = client.get('/api/agent/sessions/' + session_id).json()
    assert 'context_window' in record
    assert 'context_window' in client.get('/api/models').json()['profiles'][0]
    adapter = rt.RUNTIMES[session_id]
    adapter.event({'type': 'assistant_started'})
    adapter.event({'type': 'usage', 'result': {}})
    assert client.get('/api/agent/sessions/' + session_id).json()['usage'] == record['usage']
    adapter.event({'type': 'usage', 'result': {'input_tokens': 1200, 'cache_hit_rate': None}})
    assert client.get('/api/agent/sessions/' + session_id).json()['usage']['cache_hit_rate'] is None



def test_office_screenshots_are_temporary_and_only_available_via_preview_routes(client):
    session_id = new_session(client)
    services = rt.runtime(session_id).ensure_artifact_services()
    root = d.files_root(session_id)
    for feature, extension, collection, image in (
        ('docx', 'docx', 'documents', 'page-001.png'),
        ('pptx', 'pptx', 'presentations', 'slide-001.png')):
        service = services[feature]
        if feature == 'docx': service.live_preview = False
        store = service.store(root / ('report.' + extension))
        store.init('report')
        revision = store.status()['revision']
        cache = store.render_directory(revision)
        cache.mkdir(parents=True, exist_ok=True)
        (cache / image).write_bytes(b'preview-image')
        assert not cache.is_relative_to(root)
        assert cache.is_relative_to(root.parent / 'tmp')
        service.sync()
        response = client.get(f'/api/agent/sessions/{session_id}/{collection}/report/renders/{revision}/{image}')
        assert response.status_code == 200, response.text
        assert response.content == b'preview-image'
    paths = {item['path'] for item in client.get(f'/api/agent/sessions/{session_id}/files').json()}
    assert paths == {'report.docx', 'report.pptx'}
    assert {item['path'] for item in rt.changed_files(root, {})} == paths


def test_session_output_list_is_current_and_excludes_uploads(client):
    session_id = new_session(client)
    root = d.files_root(session_id)
    report = root / 'report.txt'
    report.write_text('first')
    adapter = rt.runtime(session_id)
    adapter.event({'type':'output_files','files':[{'path':'report.txt','size':5}]})
    report.write_text('second edit')
    adapter.event({'type':'output_files','files':[{'path':'report.txt','size':11}]})
    (root/'uploads').mkdir()
    (root/'uploads'/'input.txt').write_text('input')
    assert client.get('/api/agent/sessions/'+session_id).json()['output_files'] == [{'path':'report.txt','size':11}]
    report.unlink()
    assert client.get('/api/agent/sessions/'+session_id).json()['output_files'] == []


def test_preview_catalog_indexes_all_session_office_files_without_changing_bytes(client):
    from pptx import Presentation
    from docx import Document
    from hashlib import sha256
    session_id = new_session(client)
    root = d.files_root(session_id)
    paths = []
    for folder in (root, root/'nested', root/'uploads'/'example'):
        folder.mkdir(parents=True, exist_ok=True)
        doc = Document(); doc.add_paragraph('Preview document')
        path = folder/'report.docx'; doc.save(path); paths.append(path)
        deck = Presentation(); deck.slides.add_slide(deck.slide_layouts[5])
        path = folder/'deck.pptx'; deck.save(path); paths.append(path)
    before = {path:sha256(path.read_bytes()).hexdigest() for path in paths}
    # Import only; the DOCX render worker is tested separately.
    rt.runtime(session_id).ensure_artifact_services()['docx'].live_preview = False
    for extension, collection in (('.docx','documents'),('.pptx','presentations')):
        values = client.get(f'/api/agent/sessions/{session_id}/{collection}').json()
        assert {value['path'] for value in values} == {str(path.relative_to(root)) for path in paths if path.suffix==extension}
    assert all(sha256(path.read_bytes()).hexdigest()==digest for path,digest in before.items())
    if __import__('shutil').which('libreoffice'):
        response = client.post(f'/api/agent/sessions/{session_id}/presentations/deck/preview')
        assert response.status_code == 200, response.text
        slide = response.json()['manifest']['slides'][0]
        assert slide['formal']['revision'] == 0
        assert not slide['review'].get('automatic_passed')
        assert slide['review']['visual_review'] == 'pending'


def test_native_subagent_executes_and_returns_to_parent(client, monkeypatch):
    session_id = new_session(client)
    target = files_root() / 'delegated.txt'
    def build_model(profile, **kwargs):
        model = DelegationModel(target=str(target))
        object.__setattr__(model, '_deep_agent_model_id', profile.id)
        return model
    monkeypatch.setattr('platform_app.agent_core.llm.build_chat_model', build_model)
    monkeypatch.setattr('platform_app.agent_core.runner.build_chat_model', build_model)
    assert client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'Delegate an independent file task.'}).status_code == 202
    record = client.get('/api/agent/sessions/' + session_id).json()
    assert record['status'] == 'completed', record
    assert rt.RUNTIMES[session_id].runner.settings.subagents.enabled
    assert any(e['type'] == 'subagents_changed' and e['result'].get('count') == 1 for e in d.all_records('event'))
    assert target.read_text() == 'delegated-ok'
    assert record['output'] == 'Main agent received subagent result.'


def test_global_config_freezes_session_snapshot(client, monkeypatch, tmp_path):
    import platform_app.agent_core.config
    cli = tmp_path / 'cli-skills' / 'example'
    cli.mkdir(parents=True)
    (cli / 'SKILL.md').write_text('---\nname: cli-example\ndescription: CLI skill example\n---\nInstructions')
    monkeypatch.setattr(platform_app.agent_core.config, 'default_skills_dir', lambda: cli.parent)
    monkeypatch.setattr('platform_app.agent_core.sandbox.default_skills_dir', lambda: cli.parent)
    config = client.get('/api/settings').json()
    assert {t['name'] for t in config['tools']} == set(d.TOOL_SCHEMAS)
    assert any(s['name'] == 'cli-example' and s['enabled'] for s in config['native_skills'])
    session = client.post('/api/agent/sessions', json={'model_id': 'test/model'}).json()
    snapshot = d.get('session', session['id'])['config']
    config['skills'].append({'name': 'New skill', 'content': 'A new workflow'})
    assert client.put('/api/settings', json=config).status_code == 200
    assert any(s['name'] == 'New skill' for s in client.get('/api/settings').json()['skills'])
    assert all(s['name'] != 'New skill' for s in snapshot['skills'])
    assert session['workspace_id'] == rt.resolve(session['id']).workspace_id
    assert client.post(f'/api/agent/sessions/{session["id"]}/messages', json={'text': 'hello'}).status_code == 202
    assert client.get('/api/agent/sessions/' + session['id']).json()['status'] == 'completed'
    runner = rt.RUNTIMES[session['id']].runner
    assert '/agent-skills/installed/' in runner.prepared.skill_sources
    assert runner.prepared.backend.download_files(['/agent-skills/installed/example/SKILL.md'])[0].content.endswith(b'Instructions')


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
    ws = d.get('settings', 'global')
    ws['core_tools'].update(web_search=False, web_fetch=False)
    d.put('settings', ws)
    session = new_session(client)
    client.post(f'/api/agent/sessions/{session}/messages', json={'text': 'hello'})
    hidden = rt.RUNTIMES[session].runner.prepared.model_tool_names
    assert 'web_search' not in hidden and 'web_fetch' not in hidden
    assert 'task' in hidden


def test_installed_skill_bindings_reach_native_discovery_and_freeze_resources(client, monkeypatch, tmp_path):
    import platform_app.agent_core.config
    root = tmp_path / 'installed-skills'
    for name in ('docx-author', 'other-skill'):
        path = root / name
        (path / 'scripts').mkdir(parents=True)
        (path / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: {name} description\n---\nOriginal instructions')
        (path / 'scripts' / 'helper.py').write_text('print("original helper")')
    monkeypatch.setattr(platform_app.agent_core.config, 'default_skills_dir', lambda: root)
    monkeypatch.setattr('platform_app.agent_core.sandbox.default_skills_dir', lambda: root)
    writing = client.get('/api/settings').json()
    assert {s['id'] for s in writing['native_skills'] if s['source'] == 'platform'} == {'docx-author', 'other-skill'}
    assert all(s['enabled'] for s in writing['native_skills'])
    writing['skill_bindings'] = {'docx-author': True, 'other-skill': False}
    saved = client.put('/api/settings', json=writing)
    assert saved.status_code == 200, saved.text
    assert [s['id'] for s in saved.json()['native_skills'] if s['enabled'] and s['source'] == 'platform'] == ['docx-author']
    session = new_session(client)
    assert client.post(f'/api/agent/sessions/{session}/messages', json={'text': 'hello'}).status_code == 202
    runner = rt.RUNTIMES[session].runner
    files = runner.prepared.backend.download_files(['/agent-skills/installed/docx-author/scripts/helper.py'])
    assert b'original helper' in files[0].content
    snapshot = tmp_path / 'skill-snapshots' / session / 'installed'
    assert not (snapshot / 'other-skill').exists()
    assert runner.prepared.backend.write('/agent-skills/installed/docx-author/scripts/helper.py', 'changed').error
    # A global configuration change does not modify an existing session's snapshot.
    writing = saved.json()
    writing['skill_bindings']['docx-author'] = False
    assert client.put('/api/settings', json=writing).status_code == 200
    (root / 'docx-author' / 'scripts' / 'helper.py').write_text('print("new helper")')
    assert b'original helper' in runner.prepared.backend.download_files(['/agent-skills/installed/docx-author/scripts/helper.py'])[0].content
    assert d.get('session', session)['config']['skill_bindings']['docx-author']
    assert not any(s['enabled'] for s in client.get('/api/settings').json()['native_skills'] if s['source'] == 'platform')


def test_skill_binding_validation(client):
    config = client.get('/api/settings').json()
    for invalid in ({'../escape': True}, {'not-installed': True}, {'fake': 'yes'}):
        config['skill_bindings'] = invalid
        assert client.put('/api/settings', json=config).status_code == 422


def test_bundled_office_skills_are_listed_and_disable_for_new_sessions(client):
    settings = client.get('/api/settings').json()
    bundled = {skill['id']: skill for skill in settings['native_skills'] if skill['source'] == 'feature'}
    assert set(bundled) == {'docx-ooxml-author', 'pptx-ooxml-author'}
    assert all(skill['enabled'] and skill['description'] for skill in bundled.values())
    settings['skill_bindings'] = {identity: False for identity in bundled}
    assert client.put('/api/settings', json=settings).status_code == 200
    session_id = new_session(client)
    assert client.post(f'/api/agent/sessions/{session_id}/messages', json={'text':'hello'}).status_code == 202
    native = rt.RUNTIMES[session_id].runner
    assert not any(name.startswith(('docx_', 'pptx_')) for name in native.prepared.model_tool_names)
    assert set(rt.RUNTIMES[session_id].artifact_services) == {'docx', 'pptx'}  # read-only preview stays available
    assert set(client.get('/api/agent/sessions/' + session_id).json()['enabled_features']) == {'docx', 'pptx'}
    from platform_app.workspace import resolve
    assert all(not (resolve(session_id).skills_root / identity).exists() for identity in bundled)


def test_pptx_skill_cli_runs_inside_native_sandbox(client):
    from platform_app.workspace import resolve
    session_id = new_session(client)
    native = rt.runtime(session_id).build()
    assert not any(name.startswith(('docx_', 'pptx_')) for name in native.prepared.model_tool_names)
    context = resolve(session_id)
    backend = native.prepared.backend
    script = 'python /agent-skills/pptx-ooxml-author/scripts/office_pptx.py'
    prefix = 'cd ' + shlex.quote(str(context.files_root)) + ' && '
    response = backend.execute(prefix + script + ' init deck.pptx --presentation-id deck')
    assert response.exit_code == 0, response.output
    request = '{"presentation_id":"deck","operation_id":"slide-1","base_revision":0,"slide_id":"s1","operation":"slide.add","layout":"title_body"}'
    response = backend.execute(prefix + script + " apply-one deck.pptx --request /dev/stdin <<'JSON'\n" + request + '\nJSON')
    assert response.exit_code == 0, response.output
    response = backend.execute(prefix + script + ' check-slide deck.pptx --slide-id s1 --notes "Blank slide runtime check"')
    assert response.exit_code == 0, response.output
    import json
    result = json.loads(response.output)
    # Even if rendering dependencies are absent, the CLI returns a structured
    # failure instead of falsely marking a slide as reviewed.
    assert 'review' in result and 'rendered' in result['review']
    if __import__('shutil').which('libreoffice'):
        assert result['review']['rendered'], result['review']
    assert __import__('pathlib').Path(result['render_directory']).is_relative_to(context.workspace_root / 'tmp')
    rt.runtime(session_id).artifact_services['pptx'].sync()
    assert client.get(f'/api/agent/sessions/{session_id}/presentations').json()[0]['revision'] == 1


def test_docx_revisions_are_journaled_during_real_runner_invocation(client, monkeypatch):
    """Every next model call can already read the previous commit in the journal."""
    import json
    DocumentStore = format_plugins.load_plugin('docx').DocumentStore

    class StreamingDocxModel(BaseChatModel):
        @property
        def _llm_type(self): return 'streaming-docx-test'
        def bind_tools(self, tools, **kwargs):
            names = [t.get('name') if isinstance(t, dict) else t.name for t in tools]
            assert 'execute' in names and not any(name.startswith(('docx_', 'pptx_')) for name in names)
            return self
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            completed = [m for m in messages if isinstance(m, ToolMessage)]
            root = files_root()
            if not completed:
                command = 'python /agent-skills/docx-ooxml-author/scripts/office_docx.py init --document-id report --output report.docx'
            else:
                state = DocumentStore(root / 'report.docx').status()
                revisions = [e for e in d.all_records('event') if e['type'] in ('document_initialized','document_revision')]
                assert len(revisions) >= state['revision'] + 1
                if state['revision'] >= 2:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content='Draft ready; visual review pending.'))])
                n = state['revision'] + 1
                request = dict(document_id='report', operation_id=f'op-{n}', base_revision=n-1,
                    operation=dict(type='append_paragraph', block_id=f'p-{n}', text=f'Paragraph {n}'))
                command = 'python /agent-skills/docx-ooxml-author/scripts/office_docx.py apply-one report.docx /dev/stdin <<\'JSON\'\n' + json.dumps(request) + '\nJSON'
            command = 'cd ' + shlex.quote(str(root)) + ' && ' + command
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[dict(name='execute', args={'command':command}, id=f'doc-{len(completed)}', type='tool_call')]))])

    monkeypatch.setattr('platform_app.agent_core.llm.build_chat_model', lambda *args, **kwargs: StreamingDocxModel())
    monkeypatch.setattr('platform_app.agent_core.runner.build_chat_model', lambda *args, **kwargs: StreamingDocxModel())
    session_id = new_session(client)
    response = client.post(f'/api/agent/sessions/{session_id}/messages', json={'text': 'Write a report incrementally'})
    assert response.status_code == 202, response.text
    for _ in range(3):
        assert approve(client, session_id).status_code == 202
    assert client.get(f'/api/agent/sessions/{session_id}').json()['status'] == 'completed'
    journal = client.get(f'/api/agent/sessions/{session_id}/journal').json()['events']
    updates = [e for e in journal if e['type'] in ('document_initialized', 'document_revision')]
    assert [e['revision'] for e in updates] == list(range(3))
    assert updates[-1]['seq'] < max(e['seq'] for e in journal if e['type'] == 'result')
    docs = client.get(f'/api/agent/sessions/{session_id}/documents').json()
    assert docs[0]['revision'] == 2
    assert client.get(f'/api/agent/sessions/{session_id}/documents/report/revisions/2').content.startswith(b'PK')
    # Rebuild runtime without losing latest version or replaying old store events.
    runtime = rt.RUNTIMES[session_id]
    runtime.artifact_services['docx'].close(); runtime.runner.close(); runtime.runner.session_store.close(); rt.RUNTIMES.clear()
    rt.initialize()
    assert client.get(f'/api/agent/sessions/{session_id}/documents').json()[0]['revision'] == 2
    rebuilt = rt.runtime(session_id); rebuilt.build(); rebuilt.artifact_services['docx'].sync()
    assert len([e for e in d.all_records('event') if e['type'] in ('document_initialized','document_revision')]) == 3
    # Internal version database is never downloadable/listed in the file library.
    assert all('.docx-state' not in item['path'] for item in client.get(file_api()).json())
    assert client.get(file_api() + '/.docx-state/report.docx/history.sqlite').status_code == 404
    if __import__('shutil').which('libreoffice'):
        response = rebuilt.runner.prepared.backend.execute('cd ' + shlex.quote(str(files_root())) +
            ' && python /agent-skills/docx-ooxml-author/scripts/office_docx.py checkpoint report.docx')
        assert response.exit_code == 0, response.output
        review = json.loads(response.output)
        assert review['render_error'] is None, review
        assert review['pages']


def test_docx_external_tool_boundary_tracking_and_visual_capability(client):
    Documents = format_plugins.load_plugin('docx').Documents
    DocumentStore = format_plugins.load_plugin('docx').DocumentStore
    from dataclasses import dataclass
    session_id = new_session(client)
    runtime = rt.runtime(session_id)
    root = files_root(); root.mkdir(parents=True, exist_ok=True)
    runtime.artifact_services['docx'] = Documents(root, lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))))
    runtime.artifact_services['docx'].store(root / 'report.docx').init('report')
    runtime.artifact_services['docx'].store(root / 'report.docx').apply_one(dict(document_id='report', base_revision=0, operation_id='p1',
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
    assert not hasattr(runtime.artifact_services['docx'], 'execute')
    assert client.get(f'/api/agent/sessions/{session_id}/documents').json()[0]['revision'] == 2
    other = new_session(client)
    assert client.get(f'/api/agent/sessions/{other}/documents/report/revisions/2').status_code == 404


def test_docx_steering_persists_viewed_revision_and_uses_current_version(client):
    Documents = format_plugins.load_plugin('docx').Documents
    from unittest.mock import Mock
    session_id = new_session(client); runtime = rt.runtime(session_id)
    root = files_root()
    runtime.artifact_services['docx'] = Documents(root, lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))))
    runtime.artifact_services['docx'].store(root / 'report.docx').init('report')
    runtime.artifact_services['docx'].store(root / 'report.docx').apply_one(dict(document_id='report', base_revision=0, operation_id='p1',
        operation=dict(type='append_paragraph', block_id='p1', text='Original')))
    runtime.artifact_services['docx'].sync()
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
    Documents = format_plugins.load_plugin('docx').Documents
    from docx import Document
    session_id=new_session(client); runtime=rt.runtime(session_id)
    root=files_root(); (root/'reports').mkdir(parents=True)
    document=Document(); document.add_paragraph('Native nested report'); document.save(root/'reports'/'report.docx')
    runtime.artifact_services['docx']=Documents(root,lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))))
    runtime.artifact_services['docx'].detect_external()
    result=client.get(f'/api/agent/sessions/{session_id}/documents').json()
    assert result[0]['path']=='reports/report.docx' and result[0]['revision']==0
    assert client.get(f'/api/agent/sessions/{session_id}/documents/{result[0]["document_id"]}/revisions/0').content.startswith(b'PK')


def test_background_preview_follows_the_last_commit(client, monkeypatch):
    import threading
    import time
    import pymupdf as fitz
    Documents = format_plugins.load_plugin('docx').Documents
    engine = format_plugins.load_plugin('docx').engine
    started=threading.Event(); release=threading.Event()
    def render(docx, output, dpi=125):
        started.set(); assert release.wait(5)
        doc=fitz.open(); page=doc.new_page(); page.insert_text((72,72),'Actual one-page render fixture')
        doc.save(output/'preview.pdf'); page.get_pixmap().save(output/'page-001.png'); doc.close()
    monkeypatch.setattr(engine.o,'run_render',render)
    session_id=new_session(client); runtime=rt.runtime(session_id)
    root=files_root()
    runtime.artifact_services['docx']=Documents(root,lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))),live_preview=True)
    docs=runtime.artifact_services['docx']
    doc_store = docs.store(root / 'report.docx')
    doc_store.init('report')
    docs.sync(); docs.schedule_preview(doc_store.path)
    assert started.wait(5)
    for i in range(3):
        doc_store.apply_one(dict(document_id='report',base_revision=i,operation_id=f'op-{i}',operation=dict(type='append_paragraph',block_id=f'p-{i}',text=f'Short formatted fixture {i}')))
        docs.sync(); docs.schedule_preview(doc_store.path)
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
    Documents = format_plugins.load_plugin('docx').Documents
    session_id=new_session(client); runtime=rt.runtime(session_id)
    root=files_root()
    runtime.artifact_services['docx']=Documents(root,lambda event: runtime.event(dict(event, artifact_extension=dict(feature='docx', id=event.get('document_id'), state_key='documents'))))
    runtime.artifact_services['docx'].store(root / 'report.docx').init('report')
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
    root=files_root();internal=root/'.deepagents'/'conversation_history'
    internal.mkdir(parents=True);(internal/'session_private.md').write_text('internal trace')
    (root/'report.txt').write_text('public output')
    assert rt.changed_files(root,{})==[{'path':'report.txt','size':13}]
    assert client.get(file_api() + '/.deepagents/conversation_history/session_private.md').status_code==404
    assert client.get(file_api()).json()==[{'path':'report.txt','size':13}]
    event=rt.public_event(dict(type='output_files',files=[dict(path='.deepagents/conversation_history/session_private.md'),dict(path='report.txt')]))
    assert event['files']==[dict(path='report.txt')]


def test_sandbox_confirmation_pending_boundary_and_restart(client):
    session_id=new_session(client);base=f'/api/agent/sessions/{session_id}'
    assert client.put(base+'/sandbox',json={'mode':'full-access'}).status_code==422
    assert client.post('/api/agent/sessions',json={'sandbox_mode':'full-access'}).status_code==422
    assert client.post(base+'/messages',json={'text':'hello'}).status_code==202
    runner=rt.RUNTIMES[session_id].runner
    assert runner.sandbox_mode()=='restricted'
    record=d.get('session',session_id);record['status']='running';d.put('session',record)
    response=client.put(base+'/sandbox',json={'mode':'full-access','confirmation':'FULL_ACCESS'})
    assert response.json()['pending_sandbox_mode']=='full-access'
    assert runner.sandbox_mode()=='restricted'
    runner.apply_pending_config_if_idle()
    assert client.get(base).json()['sandbox_mode']=='full-access'
    assert client.get(base).json()['pending_sandbox_mode'] is None
    record=d.get('session',session_id);record['status']='completed';d.put('session',record)
    runner.close();runner.session_store.close();rt.RUNTIMES.pop(session_id)
    assert client.post(base+'/messages',json={'text':'hello again'}).status_code==202
    assert rt.RUNTIMES[session_id].runner.sandbox_mode()=='full-access'
    assert client.put(base+'/sandbox',json={'mode':'restricted'}).json()['sandbox_mode']=='restricted'




def test_unscoped_host_mcp_is_not_started(client, monkeypatch):
    config=d.get('settings','global')
    config['mcp']={'enabled':True,'servers':{'host-files':{'transport':'stdio','command':'/must-not-start','args':[]}}}
    d.put('settings',config)
    session_id=new_session(client)
    assert client.post(f'/api/agent/sessions/{session_id}/messages',json={'text':'hello'}).status_code==202
    assert client.get('/api/agent/sessions/'+session_id).json()['status']=='completed'
    assert not any('host-files' in name for name in rt.RUNTIMES[session_id].runner.prepared.model_tool_names)
    assert any(e['type']=='resource_policy' and e['component']=='mcp' and e['status']=='blocked' for e in rt.journal(session_id,0))


def test_failed_native_sandbox_rebuild_keeps_old_policy_and_clears_pending(client,monkeypatch):
    session_id=new_session(client);base=f'/api/agent/sessions/{session_id}'
    client.post(base+'/messages',json={'text':'hello'})
    backend=rt.RUNTIMES[session_id].runner.prepared.backend
    def fail(mode):raise ValueError('Simulated invalid mount')
    monkeypatch.setattr(backend,'apply_sandbox_mode',fail)
    response=client.put(base+'/sandbox',json={'mode':'full-access','confirmation':'FULL_ACCESS'})
    assert response.status_code==503
    record=client.get(base).json()
    assert record['sandbox_mode']=='restricted' and record['pending_sandbox_mode'] is None
    assert any(e['type']=='runtime_config_failed' for e in rt.journal(session_id,0))
