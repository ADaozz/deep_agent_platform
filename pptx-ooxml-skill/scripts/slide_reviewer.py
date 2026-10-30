"""Formal Impress screenshots and slide-level acceptance; never pretend visual QA."""
import hashlib
import fcntl
import io
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
HERE=Path(__file__).resolve().parent
for folder in (ROOT,HERE):
    if str(folder) not in sys.path:sys.path.insert(0,str(folder))
from design import design_quality, layout_registry, profile_registry
import component_validator


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


def design_context(state, slide_id, prs):
    try:
        profile = state.get('profile') or profile_registry.load_registry().get(state.get('theme_name') or 'corporate_blue')
    except ValueError:
        profile = None
    slots=None
    layout_id=state.get('layouts',{}).get(slide_id)
    if layout_id:
        try:
            modules=state.get('layout_modules',{}).get(slide_id,0)
            key,definitions=layout_registry.load_layouts().variant_for(layout_id,modules)
            resolved=state.get('layout_slots',{}).get(slide_id,{})
            placeholders=state.get('slots',{}).get(slide_id,{})
            if definitions:
                slots={name:dict(capacity=slot['capacity'],role=slot['role'],rect=resolved.get(name)) for name,slot in definitions.items()}
                for name in placeholders:
                    slots.setdefault(name,dict(capacity={},role='body',rect=placeholders[name]))
        except (ValueError,KeyError):
            slots=None
    registry_error=None
    try:
        components=component_validator.load_registry()
    except ValueError as exc:
        components=None;registry_error=str(exc)[:200]
    return profile,components,slots,registry_error


INTEGRITY_TYPES={'render_failed','rendered_text_missing','out_of_bounds','text_overflow','unexpected_overlap','missing_font','font_measurement_unavailable',
    'connector_unbound','connector_dangling','connector_endpoint_missing','connector_crosses_text','arrow_direction_mismatch','connector_meaningless_short'}
CARRIED_AESTHETIC={'passed','failed','uncertain'}


def effective_aesthetic(review):
    aesthetic=(review or {}).get('aesthetic_review') or {}
    decision=(aesthetic.get('human') or {}).get('decision')
    if decision=='accept':return 'passed'
    if decision=='reject':return 'failed'
    return aesthetic.get('status','pending')


def derive_status(review):
    """blocked: deterministic errors; needs_attention: model/human found real defects."""
    if not review.get('automatic_passed'):return 'blocked'
    value=effective_aesthetic(review)
    return {'passed':'passed','failed':'needs_attention'}.get(value,'pending')


def compat_visual(review):
    value=effective_aesthetic(review)
    return value if value in ('passed','failed') else 'pending'


def split_review(issues,rendered,render_error):
    errors=lambda items:[issue for issue in items if issue.get('severity','error')=='error']
    integrity=[issue for issue in issues if issue.get('type') in INTEGRITY_TYPES]
    technical=[issue for issue in issues if issue.get('type') not in INTEGRITY_TYPES]
    return (dict(status='failed' if errors(technical) else 'passed',**design_quality.summary(technical)),
        dict(status='unavailable' if not rendered else 'failed' if errors(integrity) else 'passed',rendered=rendered,render_error=render_error,
            errors=[issue.get('element_id') or issue.get('type') for issue in errors(integrity)]))


def check(store,slide_id,content_notes,intent=None,constraints=None):
    if not isinstance(content_notes,str) or not content_notes.strip():raise ValueError('Read slide content and provide concrete content-review notes')
    if intent is not None and (not isinstance(intent,str) or len(intent)>500):raise ValueError('intent is one short description of the page purpose (<=500 characters)')
    constraints=[item.strip() for item in (constraints or []) if isinstance(item,str) and item.strip()]
    if len(constraints)>6 or any(len(item)>200 for item in constraints):raise ValueError('At most 6 user layout constraints of <=200 characters each')
    with store.locked() as(c,state):
        store.assert_current(state)
        revision=state['revision']; data=store.snapshot_unlocked(c,revision)
        prs=__import__('pptx').Presentation(io.BytesIO(data))
        views=store.view(c,state)['manifest']; selected=next((s for s in views['slides'] if s['slide_id']==slide_id),None)
        if selected is None:raise ValueError('Unknown slide')
        index=next(i for i,s in enumerate(views['slides']) if s['slide_id']==slide_id)
        profile,components,slots,registry_error=design_context(state,slide_id,prs)
    validator=__import__('importlib').util.spec_from_file_location('slide_validator',Path(__file__).with_name('slide_validator.py'))
    module=__import__('importlib').util.module_from_spec(validator); validator.loader.exec_module(module)
    issues=module.layout_issues(selected,prs.slide_width/12700,prs.slide_height/12700)
    issues+=design_quality.review(selected,profile,components,slots,prs.slide_width/12700,prs.slide_height/12700,prs.slide_width/914400,prs.slide_height/914400)
    if registry_error and any(element.get('type')=='component' for element in selected['elements']):
        issues.append(dict(type='component_registry_invalid',severity='error',message='Component registry is invalid: '+registry_error))
    render_error=None; image_name=None; rendered=False
    directory=store.render_directory(revision)
    directory.mkdir(parents=True,exist_ok=True)
    fingerprint=None
    try:
        import pymupdf as fitz
        directory=render_snapshot(store, revision)
        pdf=directory/(store.path.stem+'.pdf')
        with fitz.open(pdf) as doc:
            if len(doc)!=len(prs.slides):raise ValueError('Rendered slide count mismatch')
            page=doc[index]; image_name=f'slide-{index+1:03d}.png'
            text=''.join(page.get_text().split())
            for element in selected['elements']:
                if element['type']=='text' or (element['type']=='shape' and element.get('text')):
                    for paragraph in element['text'].split('\n'):
                        if paragraph.strip() and ''.join(paragraph.split()) not in text:issues.append(dict(type='rendered_text_missing',severity='error',element_id=element['id']))
            fingerprint=hashlib.sha256(page.get_pixmap(alpha=False).samples).hexdigest(); rendered=True
    except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired,ImportError) as exc:
        render_error=type(exc).__name__+': '+str(exc)[:200]; issues.append(dict(type='render_failed',severity='error'))
    errors=[issue for issue in issues if issue.get('severity','error')=='error']
    with store.locked() as(c,state):
        store.assert_current(state,revision)
        if errors:state['repair_attempts'][slide_id]=state['repair_attempts'].get(slide_id,0)+1
        else:state['repair_attempts'][slide_id]=0
        old=state['reviews'].get(slide_id,state.get('last_reviews',{}).get(slide_id,{}))
        previous_intent=old.get('intent') if old else None
        intent=intent if intent is not None else previous_intent
        constraints=constraints or (old.get('constraints') if old else None) or []
        technical,integrity=split_review(issues,rendered,render_error)
        aesthetic=dict(status='pending',reason='awaiting_vision_review' if not errors else 'blocked_by_automatic_checks')
        carried=old.get('aesthetic_review') or {}
        # Same pixels, same intent: the earlier model/human verdict still describes this page.
        if (not errors and fingerprint and old.get('fingerprint')==fingerprint and carried.get('status') in CARRIED_AESTHETIC
                and old.get('intent')==intent and (old.get('constraints') or [])==constraints):
            aesthetic=dict(carried,carried_from_revision=carried.get('revision',old.get('revision')))
        review=dict(slide_id=slide_id,revision=revision,automatic_passed=not errors,rendered=rendered,layout_issues=issues,content_notes=content_notes,
            render_error=render_error,fingerprint=fingerprint,repair_attempts=state['repair_attempts'][slide_id],
            design=design_quality.summary(issues),technical_review=technical,visual_integrity=integrity,aesthetic_review=aesthetic,
            intent=intent,constraints=constraints)
        if old.get('agent_inspection') and old.get('fingerprint')==fingerprint:review['agent_inspection']=old['agent_inspection']
        review['status']=derive_status(review);review['visual_review']=compat_visual(review)
        state['reviews'][slide_id]=review
        if rendered:state['renders'][slide_id]=dict(revision=revision,image=image_name)
        event=dict(type='presentation_review',presentation_id=state['presentation_id'],revision=revision,slide_id=slide_id,review=review,
            manifest=store.view(c,state)['manifest'],path=store.path.name,render_directory=str(directory))
        return store.emit_state(c,state,event)
