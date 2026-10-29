"""Legacy data imports preserve history without keeping workspace behavior."""
import copy
from pathlib import Path
import pytest

from fastapi.testclient import TestClient
from platform_app import domain as d, format_plugins, runtime
from platform_app.main import app
from platform_app.documents import DocumentStore


def legacy_config(identity):
    return {**d.defaults(), 'id': identity, 'name': identity, 'bind_all': identity == 'everything'}


def test_legacy_import_is_repeatable_preserves_collisions_and_references(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'DATA', tmp_path)
    for identity in ('office', 'writing', 'everything'):
        config = legacy_config(identity)
        if identity == 'office':
            config['skills'] = [{'name': 'Imported skill', 'content': 'Preserve this workflow'}]
        d.put('workspace', config)
    for identity in ('office', 'writing'):
        old = tmp_path / 'workspaces' / identity / 'files'
        old.mkdir(parents=True)
        (old / 'same.txt').write_text(identity)
        d.put('upload', dict(id=f'upload-{identity}', workspace_id=identity, path='same.txt', name='same.txt'))
        d.put('session', dict(id=f'session-{identity}', workspace_id=identity, config=legacy_config(identity), status='completed', messages=[dict(attachments=[dict(path='same.txt')])]))
        artifacts = tmp_path / 'artifacts' / identity / f'session-{identity}'
        artifacts.mkdir(parents=True)
        (artifacts / 'result.txt').write_text(identity)
        d.put('event', dict(id=f'session-{identity}:0000000001', session_id=f'session-{identity}', seq=1, type='output_files', workspace_id=identity, files=[dict(path='same.txt', size=6)]))
    d.init()
    client = TestClient(app)
    for identity in ('office', 'writing'):
        assert client.get('/api/agent/files/imported/' + identity + '/same.txt').text == identity
        session = client.get('/api/agent/sessions/session-' + identity).json()
        assert 'workspace_id' not in session
        assert session['artifacts'] == ['result.txt']
        assert client.get(f'/api/agent/sessions/session-{identity}/artifacts/result.txt').text == identity
        assert d.get('upload', f'upload-{identity}')['path'] == f'imported/{identity}/same.txt'
        event = client.get(f'/api/agent/sessions/session-{identity}/journal').json()['events'][0]
        assert event['files'][0]['path'] == f'imported/{identity}/same.txt'
        assert 'workspace_id' not in event
    assert d.all_records('workspace') == []
    assert d.get('settings', 'global')['skills'][0]['name'] == 'Imported skill'
    # Re-running init neither copies stale backup content over edits nor repeats prefixes.
    (d.files_root() / 'imported/office/same.txt').write_text('new content')
    snapshot = copy.deepcopy(d.all_records('session'))
    d.init()
    assert d.all_records('session') == snapshot
    assert (d.files_root() / 'imported/office/same.txt').read_text() == 'new content'
    assert (tmp_path / 'workspaces/office/files/same.txt').read_text() == 'office'


def test_imported_docx_and_pptx_keep_native_versions_and_can_continue(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'DATA', tmp_path)
    runtime.RUNTIMES.clear()
    pptx = format_plugins.load_plugin('pptx')
    for identity in ('office', 'writing'):
        actor = f'session-{identity}'
        config = legacy_config(identity)
        d.put('workspace', config)
        old = tmp_path / 'workspaces' / identity / 'files'
        old.mkdir(parents=True)
        document = DocumentStore(old / 'report.docx')
        document.init('report')
        document.apply_one(dict(document_id='report', operation_id='title', base_revision=0,
            operation=dict(type='append_paragraph', block_id='title', text=identity, style='Heading1')), old)
        presentation = pptx.create_service(old, lambda event: None, actor)
        presentation.execute('init', dict(presentation_id='report'))
        presentation.execute('apply_one', dict(presentation_id='report', operation_id='slide', base_revision=0, operation='slide.add', slide_id='s1'))
        d.put('session', dict(id=actor, workspace_id=identity, config=config, status='completed', messages=[],
            documents={'report': dict(path='report.docx', revision=1)}, presentations={'report': dict(path='report.pptx', revision=1)},
            artifact_cursors={'docx': {'report.docx': 0}, 'pptx': {'report.pptx': 0}}))
    d.init()
    client = TestClient(app)
    try:
        for identity in ('office', 'writing'):
            actor = f'session-{identity}'
            root = d.files_root() / 'imported' / identity
            service = runtime.runtime(actor).ensure_artifact_services()
            service['docx'].live_preview = False  # This test verifies migration, not rendering.
            assert service['docx'].execute('status', dict(document_id='report'))['revision'] == 1
            service['docx'].execute('apply_one', dict(document_id='report', operation_id='more', base_revision=1,
                operation=dict(type='append_paragraph', block_id='more', text='Continued content')))
            assert DocumentStore(root / 'report.docx').status()['revision'] == 2
            assert DocumentStore(tmp_path / 'workspaces' / identity / 'files/report.docx').status()['revision'] == 1
            service['pptx'].execute('apply_one', dict(presentation_id='report', operation_id='title', base_revision=1,
                operation='element.add_text', slide_id='s1', element=dict(id='title', text=identity, geometry=dict(x=.1,y=.1,w=.8,h=.2))))
            assert pptx.PresentationStore(root / 'report.pptx').status()['revision'] == 2
            assert client.get(f'/api/agent/sessions/{actor}/documents/report/revisions/1').status_code == 200
            assert client.get(f'/api/agent/sessions/{actor}/presentations/report/revisions/1').status_code == 200
            assert client.get(f'/api/agent/sessions/{actor}/presentations').json()[0]['path'] == f'imported/{identity}/report.pptx'
            assert service['pptx'].execute('status', dict(presentation_id='report'))['path'] == f'imported/{identity}/report.pptx'
        for identity in ('office', 'writing'):
            rt = runtime.runtime(f'session-{identity}')
            for service in rt.artifact_services.values(): service.sync()
            record = d.get('session', f'session-{identity}')
            assert record['documents']['report']['path'] == f'imported/{identity}/report.docx'
            assert record['presentations']['report']['path'] == f'imported/{identity}/report.pptx'
    finally:
        for rt in runtime.RUNTIMES.values():
            for service in rt.artifact_services.values():service.close()
        runtime.RUNTIMES.clear()


def test_failed_import_can_retry_without_publishing_partial_files(tmp_path, monkeypatch):
    from platform_app import migrations
    monkeypatch.setattr(d, 'DATA', tmp_path)
    d.put('workspace', legacy_config('office'))
    old = tmp_path / 'workspaces/office/files'
    old.mkdir(parents=True)
    (old / 'notes.txt').write_text('Complete original content')
    def broken_copy(source, target):
        Path(target).write_text('partial')
        raise OSError('Simulated disk error')
    with monkeypatch.context() as patch:
        patch.setattr(migrations.shutil, 'copy2', broken_copy)
        with pytest.raises(OSError): d.init()
    assert d.all_records('workspace')
    assert not d.all_records('settings')
    assert not (d.files_root() / 'imported/office/notes.txt').exists()
    d.init()
    assert not d.all_records('workspace')
    assert (d.files_root() / 'imported/office/notes.txt').read_text() == 'Complete original content'
