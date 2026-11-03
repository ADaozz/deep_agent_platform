"""External saves become immutable versions, render asynchronously and retain two snapshots."""
import importlib.util
import io
from pathlib import Path
import shutil
import threading
import time

import pytest
from pptx import Presentation
from pptx.util import Inches

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('external_pptx_adapter',ROOT/'platform_adapter.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
engine=adapter.engine


def deck_bytes(title):
    prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1),Inches(1),Inches(7),Inches(1)).text=title
    out=io.BytesIO();prs.save(out);return out.getvalue()


def test_external_local_edit_preserves_other_slide_reviews_and_detects_shared_assets(tmp_path):
    path=tmp_path/'deck.pptx';prs=Presentation()
    for text in ('First','Second'):
        slide=prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1),Inches(1),Inches(7),Inches(1)).text=text
    prs.save(path);store=engine.PresentationStore(path);store.track_external('deck')
    ids=[s['slide_id'] for s in store.status()['manifest']['slides']]
    with store.locked() as(c,state):
        state['reviews']={sid:dict(status='passed',automatic_passed=True,content_notes='verified',revision=0) for sid in ids}
        store.save_state(c,state);c.commit()
    prs=Presentation(path);before=prs.slides[1]._element.xml
    prs.slides[0].shapes[0].text='Local edit';prs.save(path)
    event=store.track_external('deck')
    assert event['changed_slides']==[ids[0]]
    assert Presentation(path).slides[1]._element.xml==before
    assert store.status()['manifest']['slides'][1]['review']['automatic_passed']
    assert store.status()['manifest']['slides'][0]['review']['status']=='writing'
    # A theme is a shared resource: changing it really does affect both pages.
    prs=Presentation(path)
    part=prs.slide_masters[0].part.part_related_by(engine.RT.THEME)
    part._blob=part.blob.replace(b'Arial',b'Calibri')
    if part._blob==part.blob:
        part._blob=part.blob.replace(b'</a:theme>',b'<!--changed--></a:theme>')
    prs.save(path)
    assert set(store.track_external('deck')['changed_slides'])==set(ids)


def test_deferred_batch_vision_is_not_scheduled_by_events_or_restart(tmp_path,monkeypatch):
    path=tmp_path/'deck.pptx';path.write_bytes(deck_bytes('Draft'))
    store=engine.PresentationStore(path);store.track_external('deck')
    sid=store.status()['manifest']['slides'][0]['slide_id']
    review=dict(automatic_passed=True,rendered=True,revision=0,fingerprint='pixels',
                aesthetic_review=dict(status='pending',reason='deferred_until_deck_checked'))
    with store.locked() as(c,state):
        state['reviews'][sid]=review;store.save_state(c,state);c.commit()
    service=type('Service',(),{'temporary_root':tmp_path})()
    coordinator=adapter.ReviewCoordinator(service,{'private_root':tmp_path/'private'})
    scheduled=[]
    monkeypatch.setattr(coordinator,'schedule',lambda *args:scheduled.append(args))
    event=dict(type='presentation_review',slide_id=sid,review=review)
    try:
        coordinator.observe(path,event);coordinator.resume(path)
        assert scheduled==[]
        review['aesthetic_review']['reason']='awaiting_vision_review'
        coordinator.observe(path,event)
        assert len(scheduled)==1
    finally:coordinator.close()


@pytest.mark.skipif(not shutil.which('libreoffice'),reason='LibreOffice required')
def test_whole_deck_overlap_repair_defers_vision_and_rechecks_only_changed_page(tmp_path,monkeypatch):
    path=tmp_path/'deck.pptx';prs=Presentation()
    for text in ('First','Second'):
        slide=prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1),Inches(1),Inches(5),Inches(1)).text=text
    prs.slides[0].shapes.add_textbox(Inches(1),Inches(1),Inches(5),Inches(1)).text='Overlapping'
    prs.save(path);store=engine.PresentationStore(path);store.track_external('deck')
    ids=[s['slide_id'] for s in store.status()['manifest']['slides']]
    notes={sid:'Verified page title and text against source' for sid in ids}
    result=store.check_deck(notes)
    assert result['status']=='needs_repair' and not result['overlap_passed']
    assert result['repair_targets'][0]['slide_id']==ids[0]
    assert len(result['repair_targets'][0]['elements'])==2
    assert store.status()['manifest']['slides'][1]['review']['aesthetic_review']['reason']=='deferred_until_deck_checked'
    prs=Presentation(path);unchanged=prs.slides[1]._element.xml
    prs.slides[0].shapes[1].top=Inches(3);prs.save(path)
    assert store.track_external('deck')['changed_slides']==[ids[0]]
    original=engine.reviewer().check;checked=[]
    def checking(*args,**kwargs):
        checked.append(args[1]);return original(*args,**kwargs)
    monkeypatch.setattr(engine.reviewer(),'check',checking)
    result=store.check_deck(notes)
    assert result['status']=='passed' and result['overlap_passed']
    assert checked==[ids[0]]
    assert Presentation(path).slides[1]._element.xml==unchanged
    status=store.status()
    assert all(s['review']['aesthetic_review']['reason']=='awaiting_vision_review' for s in status['manifest']['slides'])
    assert status['deck_review']['status']=='pending'


def until(predicate,timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.05)
    raise AssertionError('Preview did not reach the latest revision')


def test_external_versions_are_deduplicated_and_never_published_over_writer(tmp_path,monkeypatch):
    path=tmp_path/'deck.pptx';first=deck_bytes('First');second=deck_bytes('Second')
    path.write_bytes(first);store=engine.PresentationStore(path)
    monkeypatch.setattr(store,'_publish',lambda *args:pytest.fail('External adoption must never write the source file'))
    assert store.track_external('deck')['revision']==0
    assert store.track_external('deck') is None
    path.write_bytes(second);assert store.track_external('deck')['revision']==1
    assert store.snapshot(0)==first and store.snapshot(1)==second and path.read_bytes()==second
    path.write_bytes(first);assert store.track_external('deck')['revision']==2
    for revision in range(3):
        directory=store.render_directory(revision);directory.mkdir(parents=True);(directory/'slide-001.png').write_bytes(b'cached')
    assert store.retain_recent_history()==[2,1]
    assert [h['revision'] for h in store.status()['history']]==[1,2]
    with pytest.raises(ValueError):store.snapshot(0)
    assert store.snapshot(1)==second and store.snapshot(2)==first
    assert not store.render_directory(0).exists() and store.render_directory(1).exists()
    assert len(store.events())>=3


def test_partial_or_changing_write_never_replaces_last_good_snapshot(tmp_path,monkeypatch):
    path=tmp_path/'deck.pptx';first=deck_bytes('First');path.write_bytes(first)
    store=engine.PresentationStore(path);store.track_external('deck')
    path.write_bytes(b'PK partial write')
    with pytest.raises(Exception):store.track_external('deck')
    assert store.snapshot(0)==first and path.read_bytes()==b'PK partial write'
    second=deck_bytes('Second');third=deck_bytes('Third');path.write_bytes(second)
    original=engine.validator.validate_package
    def changing(data):
        result=original(data);path.write_bytes(third);return result
    monkeypatch.setattr(engine.validator,'validate_package',changing)
    assert store.track_external('deck') is None
    assert store.status()['revision']==0 and path.read_bytes()==third


@pytest.mark.skipif(not shutil.which('libreoffice'),reason='LibreOffice required')
def test_script_overwrite_versions_previews_and_frontend_events(tmp_path):
    root=tmp_path/'files';root.mkdir();path=root/'deck.pptx';events=[]
    service=adapter.create_service(root,events.append,temporary_root=tmp_path/'renders',host={'private_root':tmp_path/'private'})
    try:
        for revision,title in enumerate(['First','Second','Third']):
            data=deck_bytes(title);path.write_bytes(data);service.detect_external()
            until(lambda:any(e['type']=='presentation_preview' and e['revision']==revision for e in events))
            store=service.store(path);state=store.status();assert state['revision']==revision
            formal=state['manifest']['slides'][0]['formal'];assert formal['revision']==revision
            assert (store.render_directory(revision)/formal['image']).is_file()
            assert path.read_bytes()==data
        until(lambda:len(service.store(path).status()['history'])==2)
        assert [h['revision'] for h in service.store(path).status()['history']]==[1,2]
        assert not service.store(path).render_directory(0).exists()
        assert not any(e['type']=='presentation_error' for e in events)
        # The same manifest/revision sent over SSE is what the frontend uses.
        latest=[e for e in events if e['type']=='presentation_preview'][-1]
        assert latest['manifest']['slides'][0]['formal']['revision']==2
    finally:service.close()


@pytest.mark.skipif(not shutil.which('libreoffice'),reason='LibreOffice required')
def test_a_superseded_inflight_preview_cannot_replace_latest(tmp_path,monkeypatch):
    root=tmp_path/'files';root.mkdir();path=root/'deck.pptx';events=[]
    service=adapter.create_service(root,events.append,temporary_root=tmp_path/'renders',host={'private_root':tmp_path/'private'})
    entered=threading.Event();release=threading.Event();original=engine.reviewer().render_snapshot
    def slow(store,revision):
        if revision==0:
            entered.set();assert release.wait(15)
        return original(store,revision)
    monkeypatch.setattr(engine.reviewer(),'render_snapshot',slow)
    try:
        path.write_bytes(deck_bytes('First'));service.detect_external();assert entered.wait(10)
        path.write_bytes(deck_bytes('Second'));service.detect_external()
        path.write_bytes(deck_bytes('Third'));service.detect_external();release.set()
        until(lambda:any(e['type']=='presentation_preview' and e['revision']==2 for e in events))
        assert service.store(path).status()['manifest']['slides'][0]['formal']['revision']==2
        assert not any(e['type']=='presentation_preview' and e['revision']==0 for e in events)
    finally:release.set();service.close()


def test_native_chart_audit_rejects_shape_based_statistics_and_accepts_real_chart():
    prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
    from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
    slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE,Inches(1),Inches(1),Inches(2),Inches(3))
    plan={'slides':[{'page':1,'statistical_charts':1}]}
    assert engine.native_charts.audit(prs,plan)['status']=='failed'
    payload=dict(type='column_clustered',categories=['A','B'],series=[dict(name='Revenue',values=[1,2])])
    engine.native_charts.create(slide,payload,(Inches(1),Inches(1),Inches(6),Inches(4)))
    assert engine.native_charts.audit(prs,plan)['status']=='passed'
    assert engine.native_charts.audit(prs)['status']=='inventory'
