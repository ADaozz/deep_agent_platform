"""Formal Impress screenshots and slide-level acceptance; never pretend visual QA."""
import hashlib
import fcntl
import io
from pathlib import Path
import subprocess
import tempfile


def render_snapshot(store, revision):
    import pymupdf as fitz
    data = store.snapshot(revision)
    directory = store.render_directory(revision)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'render.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        source, pdf = directory / store.path.name, directory / (store.path.stem + '.pdf')
        if not pdf.exists():
            source.write_bytes(data)
            with tempfile.TemporaryDirectory(prefix='impress-profile-') as profile:
                result = subprocess.run(['libreoffice', '-env:UserInstallation=' + Path(profile).as_uri(),
                    '--headless', '--convert-to', 'pdf:impress_pdf_Export', '--outdir', str(directory), str(source)],
                    capture_output=True, text=True, timeout=120)
            if result.returncode != 0 or not pdf.is_file():
                raise RuntimeError('LibreOffice Impress conversion failed')
        with fitz.open(pdf) as doc:
            for index, page in enumerate(doc):
                image = directory / f'slide-{index+1:03d}.png'
                if not image.exists():
                    page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False).save(image)
    return directory


def check(store,slide_id,content_notes):
    if not isinstance(content_notes,str) or not content_notes.strip():raise ValueError('Read slide content and provide concrete content-review notes')
    with store.locked() as(c,state):
        store.assert_current(state)
        revision=state['revision']; data=store.snapshot_unlocked(c,revision)
        prs=__import__('pptx').Presentation(io.BytesIO(data))
        views=store.view(c,state)['manifest']; selected=next((s for s in views['slides'] if s['slide_id']==slide_id),None)
        if selected is None:raise ValueError('Unknown slide')
        index=next(i for i,s in enumerate(views['slides']) if s['slide_id']==slide_id)
    validator=__import__('importlib').util.spec_from_file_location('slide_validator',Path(__file__).with_name('slide_validator.py'))
    module=__import__('importlib').util.module_from_spec(validator); validator.loader.exec_module(module)
    issues=module.layout_issues(selected,prs.slide_width/12700,prs.slide_height/12700)
    render_error=None; image_name=None; rendered=False
    directory=store.render_directory(revision)
    directory.mkdir(parents=True,exist_ok=True)
    try:
        import pymupdf as fitz
        directory=render_snapshot(store, revision)
        pdf=directory/(store.path.stem+'.pdf')
        with fitz.open(pdf) as doc:
            if len(doc)!=len(prs.slides):raise ValueError('Rendered slide count mismatch')
            page=doc[index]; image_name=f'slide-{index+1:03d}.png'
            text=''.join(page.get_text().split())
            for element in selected['elements']:
                if element['type']=='text':
                    for paragraph in element['text'].split('\n'):
                        if paragraph.strip() and ''.join(paragraph.split()) not in text:issues.append(dict(type='rendered_text_missing',element_id=element['id']))
            fingerprint=hashlib.sha256(page.get_pixmap(alpha=False).samples).hexdigest(); rendered=True
    except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired,ImportError) as exc:
        render_error=type(exc).__name__+': '+str(exc)[:200]; issues.append(dict(type='render_failed'))
    with store.locked() as(c,state):
        store.assert_current(state,revision)
        if issues:state['repair_attempts'][slide_id]=state['repair_attempts'].get(slide_id,0)+1
        else:state['repair_attempts'][slide_id]=0
        old=state['reviews'].get(slide_id,state.get('last_reviews',{}).get(slide_id,{}))
        visual=old.get('visual_review','pending') if old.get('fingerprint')==locals().get('fingerprint') else 'pending'
        review=dict(slide_id=slide_id,revision=revision,status='blocked' if issues or visual=='failed' else 'passed' if visual=='passed' else 'pending',automatic_passed=not issues,
            rendered=rendered,layout_issues=issues,content_notes=content_notes,visual_review=visual,render_error=render_error,
            fingerprint=locals().get('fingerprint'),repair_attempts=state['repair_attempts'][slide_id])
        state['reviews'][slide_id]=review
        if rendered:state['renders'][slide_id]=dict(revision=revision,image=image_name)
        event=dict(type='presentation_review',presentation_id=state['presentation_id'],revision=revision,slide_id=slide_id,review=review,
            manifest=store.view(c,state)['manifest'],path=store.path.name,render_directory=str(directory))
        return store.emit_state(c,state,event)
