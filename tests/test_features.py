"""Independent install/uninstall must not break the Agent or the other format."""
import shutil
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from platform_app import domain as d,format_plugins


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(d,'DATA',tmp_path)
    d.init()
    from platform_app.main import app
    from platform_app import runtime
    runtime.RUNTIMES.clear()
    yield TestClient(app)
    for rt in runtime.RUNTIMES.values():
        for service in rt.artifact_services.values():service.close()
    runtime.RUNTIMES.clear()


def test_features_uninstall_independently_and_reinstall(client):
    assert {f['id'] for f in client.get('/api/features').json()}=={'docx','pptx'}
    result=client.delete('/api/features/docx');assert result.status_code==200
    rows={f['id']:f for f in result.json()};assert not rows['docx']['installed'] and rows['pptx']['enabled']
    assert 'docx' not in format_plugins.build_services(d.DATA/'files','writing','s',lambda e:None)
    assert 'pptx' in format_plugins.build_services(d.DATA/'files','writing','s',lambda e:None)
    assert client.get('/api/workspaces').status_code==200
    assert client.put('/api/features/docx',json={'enabled':True}).status_code==200
    assert client.delete('/api/features/pptx').status_code==200
    assert {m['id'] for m in format_plugins.enabled()}=={'docx'}


def test_physical_feature_directory_removal_does_not_import_engines(client,tmp_path,monkeypatch):
    root=tmp_path/'deployment';root.mkdir()
    actual=format_plugins.ROOT
    shutil.copytree(actual/'docx-ooxml-skill',root/'docx-ooxml-skill')
    # Shared module is infrastructure, not either format's Agent implementation.
    (root/'platform_app').mkdir();shutil.copy2(actual/'platform_app/artifact_runtime.py',root/'platform_app/artifact_runtime.py')
    monkeypatch.setattr(format_plugins,'ROOT',root)
    assert [m['id'] for m in format_plugins.catalog()]==['docx']
    shutil.rmtree(root/'docx-ooxml-skill')
    assert format_plugins.build_services(root/'files','writing','s',lambda e:None)=={}
    assert format_plugins.instructions()==''
    assert client.get('/api/workspaces').status_code==200


def test_deck_authorization_is_scoped_to_one_presentation_and_actor(client):
    module=format_plugins.load_plugin('pptx');root=d.DATA/'files'
    service=module.create_service(root,'writing',lambda e:None,'actor-a')
    with pytest.raises(ValueError):service.execute('apply_one',dict(presentation_id='unapproved',operation_id='op1',base_revision=0,operation='slide.add',slide_id='s1'))
    service.execute('init',dict(presentation_id='deck'))
    service.execute('apply_one',dict(presentation_id='deck',operation_id='op1',base_revision=0,operation='slide.add',slide_id='s1'))
    other=module.create_service(root,'writing',lambda e:None,'actor-b')
    with pytest.raises(ValueError):other.execute('status',dict(presentation_id='deck'))
    assert 'pptx_init' in service.approval_tools


def test_disabled_feature_skill_is_removed_from_existing_session_snapshot(client,tmp_path):
    skills=tmp_path/'skill-snapshot';skills.mkdir()
    format_plugins.snapshot_skills(skills)
    assert (skills/'docx-ooxml-author'/'SKILL.md').is_file()
    assert (skills/'pptx-ooxml-author'/'SKILL.md').is_file()
    assert client.delete('/api/features/docx').status_code==200
    format_plugins.snapshot_skills(skills)
    assert not (skills/'docx-ooxml-author').exists()
    assert (skills/'pptx-ooxml-author'/'SKILL.md').is_file()
    assert client.delete('/api/features/pptx').status_code==200
    format_plugins.snapshot_skills(skills)
    assert not (skills/'pptx-ooxml-author').exists()
