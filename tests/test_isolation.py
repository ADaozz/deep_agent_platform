"""Exercise ownership boundaries using the actual native Bubblewrap backend."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import shlex

import pytest
from fastapi.testclient import TestClient
from platform_app import domain as d, models as m, runtime, format_plugins
from platform_app.workspace import WorkspaceResolver, SandboxPolicyBuilder, PlatformPolicy, resolve
from platform_app.main import app
from platform_app.agent_core.sandbox import select_backend, SandboxUnavailableError
from platform_app.agent_core.config import BindMount


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(d,'DATA',tmp_path)
    monkeypatch.delenv('PLATFORM_POLICY',raising=False)
    d.init()
    runtime.RUNTIMES.clear()
    m.save({'default':'test/model','models':{'test':{'base_url':'https://example.com/v1','api_key':'test-only','api':'chat_completions','models':{'model':{}}}}},0)
    yield TestClient(app)
    for rt in runtime.RUNTIMES.values():
        for service in rt.artifact_services.values():service.close()
        if rt.runner:rt.runner.close();rt.runner.session_store.close()
    runtime.RUNTIMES.clear()


def thread(client):
    response=client.post('/api/agent/sessions',json={})
    assert response.status_code==201,response.text
    return response.json()['id']


def test_100_threads_resolve_concurrently_and_survive_restart(store):
    resolver=WorkspaceResolver()
    with ThreadPoolExecutor(max_workers=12) as workers:
        contexts=list(workers.map(resolver.resolve,[f'thread_{i}' for i in range(100)]))
    assert len({c.workspace_id for c in contexts})==100
    assert len({c.sandbox_state_root for c in contexts})==100
    assert [WorkspaceResolver().resolve(c.thread_id) for c in contexts]==contexts
    for c in contexts:
        assert not c.sandbox_state_root.is_relative_to(c.workspace_root)
        assert c.files_root.parent==c.workspace_root


def test_shared_project_keeps_home_and_tmp_private(store):
    a=resolve('project_a')
    # A future authorized project binder can persist this association; no user
    # API accepts a Workspace ID or arbitrary mount source.
    d.put('thread_workspace',dict(id='project_b',workspace_id=a.workspace_id))
    b=resolve('project_b')
    assert a.files_root==b.files_root
    assert a.sandbox_state_root!=b.sandbox_state_root
    assert a.skills_root!=b.skills_root


def test_policy_rejects_forged_context_storage_aliases_and_mounts(store,tmp_path,monkeypatch):
    a,b=resolve('a'),resolve('b');builder=SandboxPolicyBuilder()
    for context in (replace(a,files_root=b.files_root),replace(a,workspace_root=d.DATA),replace(a,sandbox_state_root=b.sandbox_state_root)):
        with pytest.raises(ValueError):builder.build(context)
    for path in (b.workspace_root,d.DATA/'workspaces',d.DATA,d.DATA/'sessions'):
        with pytest.raises(ValueError):builder.build(a,read_only=(BindMount(path,'/reference'),))
        with pytest.raises(ValueError):builder.build(a,read_write=(BindMount(path,'/reference'),))
    with pytest.raises(ValueError):builder.build(a,env_set={'HOME':str(b.sandbox_state_root)})
    path=tmp_path/'policy.yaml';monkeypatch.setenv('PLATFORM_POLICY',str(path))
    for raw in ({'sandbox':{'mounts':{'read_only':[str(d.DATA)]}}},{'sandbox':{'state_scope':'global'}},{'sandbox':{'full_access':{'require_confirmation':False}}}):
        path.write_text(json.dumps(raw))
        with pytest.raises(ValueError):PlatformPolicy.load()
    monkeypatch.delenv('PLATFORM_POLICY')
    alias=a.files_root/'alias';alias.symlink_to(b.files_root)
    # An alias cannot be substituted for a storage directory either.
    linked=replace(PlatformPolicy.load(),workspaces_root=alias)
    with pytest.raises(ValueError):WorkspaceResolver(linked).resolve('alias-thread')


def test_real_bubblewrap_blocks_siblings_traversal_links_and_global_skills(store):
    a,b=resolve('a'),resolve('b')
    secret=b.files_root/'private.txt';secret.write_text('THREAD_B_SECRET')
    home=b.sandbox_state_root/'sandbox/home';home.mkdir(parents=True);(home/'private').write_text('HOME_B_SECRET')
    tmp=b.sandbox_state_root/'sandbox/tmp';tmp.mkdir(parents=True);(tmp/'private').write_text('TMP_B_SECRET')
    own=a.skills_root/'SKILL.md';own.write_text('Read only workflow')
    (a.files_root/'linked').symlink_to(secret)
    (a.files_root/'owned.txt').write_text('OWNED_CONTENT')
    backend=select_backend(SandboxPolicyBuilder().build(a)).backend
    try:
        assert backend.download_files([str(a.files_root/'owned.txt')])[0].content==b'OWNED_CONTENT'
        assert backend.download_files(['/agent-skills/SKILL.md'])[0].content==b'Read only workflow'
        assert backend.write('/agent-skills/changed.txt','denied').error
        targets=[str(secret),str(home/'private'),str(tmp/'private'),str(a.files_root/'linked'),str(a.workspace_root/'..'/b.workspace_id/'files/private.txt')]
        for path in targets:
            assert backend.download_files([path])[0].error,path
            assert backend.read(path).error,path
            result=backend.execute('cat '+shlex.quote(path))
            assert result.exit_code!=0 and 'THREAD_B_SECRET' not in result.output and 'HOME_B_SECRET' not in result.output and 'TMP_B_SECRET' not in result.output
        assert backend.ls(str(b.files_root)).error
        assert backend.grep('SECRET',path=str(b.files_root)).error
        assert backend.execute('ls '+shlex.quote(str(b.files_root))).exit_code!=0
        assert backend.execute('rg SECRET '+shlex.quote(str(b.files_root))).exit_code!=0
        result=backend.execute('printf private-home > /home/agent/own; printf private-tmp > /tmp/own; test ! -e /skills; cat /agent-skills/SKILL.md')
        assert result.exit_code==0
        assert (a.sandbox_state_root/'sandbox/home/agent/own').read_text()=='private-home'
        assert not (b.sandbox_state_root/'sandbox/home/agent/own').exists()
    finally:backend.executor.pool.shutdown('test complete')


def test_full_access_and_return_to_restricted_use_native_rebuild(store,tmp_path):
    a=resolve('a');context=SandboxPolicyBuilder();private=tmp_path/'external';private.write_text('EXTERNAL')
    (a.session_root/'checkpoint-secret').write_text('CONTROL_SECRET')
    (a.skills_root/'SKILL.md').write_text('READONLY_SKILL')
    backend=select_backend(context.build(a)).backend
    try:
        assert backend.download_files([str(private)])[0].error
        context.build(a,'full-access');backend.apply_sandbox_mode('full-access')
        assert backend.download_files([str(private)])[0].content==b'EXTERNAL'
        assert backend.download_files([str(a.session_root/'checkpoint-secret')])[0].error
        assert 'CONTROL_SECRET' not in backend.execute('cat '+shlex.quote(str(a.session_root/'checkpoint-secret'))).output
        assert backend.read('/agent-skills/SKILL.md').error is None
        assert backend.write('/agent-skills/changed','denied').error
        assert backend.execute('test ! -e /skills').exit_code==0
        context.build(a,'restricted');backend.apply_sandbox_mode('restricted')
        assert backend.download_files([str(private)])[0].error
        assert backend.execute('cat '+shlex.quote(str(private))).exit_code!=0
    finally:backend.executor.pool.shutdown('test complete')


def test_bubblewrap_failure_never_runs_on_host(store):
    c=resolve('a');config=replace(SandboxPolicyBuilder().build(c),bwrap_path='/missing/bwrap')
    with pytest.raises(SandboxUnavailableError):select_backend(config)
    assert not (c.files_root/'host-executed').exists()


def test_file_api_and_office_services_never_cross_threads(store):
    a,b=thread(store),thread(store)
    response=store.post(f'/api/agent/sessions/{a}/uploads',files={'file':('private.txt',b'ONLY_A')})
    assert response.status_code==201
    upload=response.json();path=upload['path']
    assert store.get(f'/api/agent/sessions/{a}/files/'+path).content==b'ONLY_A'
    assert store.get(f'/api/agent/sessions/{b}/files/'+path).status_code==404
    assert store.post(f'/api/agent/sessions/{b}/messages',json={'text':'steal','upload_ids':[upload['id']]}).status_code==404
    assert store.get('/api/agent/files').status_code==404
    assert store.post('/api/agent/uploads',files={'file':('global.txt',b'no')}).status_code==404
    for tid in (a,b):
        service=runtime.runtime(tid).ensure_artifact_services()
        service['docx'].live_preview=False
        service['docx'].store(resolve(tid).files_root/'same.docx').init('same')
        service['pptx'].store(resolve(tid).files_root/'same.pptx').init('same')
    with ThreadPoolExecutor(max_workers=2) as pool:
        def mutate(tid):
            service=runtime.runtime(tid).ensure_artifact_services()
            service['docx'].store(resolve(tid).files_root/'same.docx').apply_one(dict(document_id='same',operation_id='title',base_revision=0,operation=dict(type='append_paragraph',block_id='title',style='Heading1',text=tid)))
            service['pptx'].store(resolve(tid).files_root/'same.pptx').apply_one(dict(presentation_id='same',operation_id='slide',base_revision=0,operation='slide.add',slide_id='one'))
            service['docx'].sync();service['pptx'].sync()
        list(pool.map(mutate,(a,b)))
    assert resolve(a).files_root!=resolve(b).files_root
    for tid,other in ((a,b),(b,a)):
        docs=store.get(f'/api/agent/sessions/{tid}/documents').json()
        assert len(docs)==1 and docs[0]['revision']==1
        journal=store.get(f'/api/agent/sessions/{tid}/journal').json()['events']
        assert len([e for e in journal if e['type']=='document_revision'])==1
        assert resolve(other).files_root.as_posix() not in json.dumps(journal)
        link=resolve(tid).files_root/'escape';link.symlink_to(resolve(other).files_root/'same.docx')
        assert store.get(f'/api/agent/sessions/{tid}/files/escape').status_code==404


def test_host_office_resources_cannot_resolve_sibling_files(store):
    import zipfile
    DocumentStore = format_plugins.load_plugin('docx').DocumentStore
    from platform_app.artifact_runtime import validate_embedded_resources
    a,b=thread(store),thread(store)
    root=resolve(a).files_root
    secret=resolve(b).files_root/'secret.txt';secret.write_text('PRIVATE_B')
    template=root/'template.docx';DocumentStore(template).init('template')
    with zipfile.ZipFile(template) as package:parts={n:package.read(n) for n in package.namelist()}
    parts['word/_rels/document.xml.rels']=b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="unsafe" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" TargetMode="External" Target="'+secret.as_uri().encode()+b'"/></Relationships>'
    with zipfile.ZipFile(template,'w') as package:
        for name,data in parts.items():package.writestr(name,data)
    service=runtime.runtime(a).ensure_artifact_services()['docx'];service.live_preview=False
    service.detect_external()
    assert not service.bound_paths
    assert not (root/'blocked.docx').exists()
    with pytest.raises(ValueError):validate_embedded_resources(template)


def test_unsupported_layout_is_rejected_without_import_or_deletion(store,tmp_path,monkeypatch):
    untouched=tmp_path/'older-data';untouched.mkdir()
    monkeypatch.setattr(d,'DATA',untouched)
    d.put('session',{'id':'old-session','status':'completed'})
    files=untouched/'files';files.mkdir();(files/'private.txt').write_text('PRESERVE_ORIGINAL')
    with pytest.raises(RuntimeError,match='PLATFORM_DATA_DIR'):d.init()
    assert (files/'private.txt').read_text()=='PRESERVE_ORIGINAL'
    assert not d.all_records('thread_workspace')
    assert not d.all_records('storage_migration')
    assert not (untouched/'workspaces').exists()


def test_legacy_task_and_global_file_api_are_absent(store):
    assert store.get('/api/tasks').status_code==404
    assert store.post('/api/tasks',json={'prompt':'removed'}).status_code==404
    assert store.get('/api/agent/files').status_code==404
    assert store.post('/api/agent/uploads',files={'file':('no.txt',b'no')}).status_code==404


def test_execute_network_request_is_effective_in_both_filesystem_modes(store):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from platform_app.agent_core.tools.execute import build_execute_tool
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.end_headers();self.wfile.write(b'HOST_PRIVATE_API')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    backend=select_backend(SandboxPolicyBuilder().build(resolve('network-thread'))).backend
    command=f'curl --noproxy "*" --max-time 2 http://127.0.0.1:{server.server_port}/private'
    tool=build_execute_tool(backend)
    try:
        for mode in ('restricted', 'full-access', 'restricted'):
            backend.apply_sandbox_mode(mode)
            denied=tool.invoke({'command':command})
            assert 'HOST_PRIVATE_API' not in denied
            allowed=tool.invoke({'command':command,'network':True})
            assert 'HOST_PRIVATE_API' in allowed,allowed
            # A subsequent command without network=true remains offline.
            denied=tool.invoke({'command':command,'network':False})
            assert 'HOST_PRIVATE_API' not in denied
    finally:
        backend.executor.pool.shutdown('network policy test complete')
        server.shutdown();server.server_close();worker.join(timeout=3)
