"""Read-only PPTX indexing, preview, host-side vision review and human review HTTP API.

The vision review runs here, in the platform process: the sandbox never sees a
model key, never supplies the screenshot and cannot mark a review as passed.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import io
import json
import logging
from pathlib import Path
import tempfile
import threading
import time
from typing import Literal
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response,FileResponse
from pydantic import BaseModel,Field
from platform_app.artifact_runtime import validate_embedded_resources

spec=importlib.util.spec_from_file_location('pptx_feature_engine',Path(__file__).parent/'scripts'/'office_pptx.py')
engine=importlib.util.module_from_spec(spec); spec.loader.exec_module(engine)
PresentationStore=engine.PresentationStore
vision_spec=importlib.util.spec_from_file_location('pptx_vision_review',Path(__file__).parent/'vision_review_service.py')
vision=importlib.util.module_from_spec(vision_spec); vision_spec.loader.exec_module(vision)
LOG=logging.getLogger(__name__)


def _title(slide):
    for element in slide.get('elements',[]):
        if element.get('type')=='text' and element.get('style_token') in ('slide_title','cover_title','section_title','statement'):return element.get('text','')[:80]
    return next((element.get('text','')[:80] for element in slide.get('elements',[]) if element.get('type')=='text' and element.get('text')),'')


class ReviewCoordinator:
    """Schedules host-rendered, model-reviewed checks; applies only current results."""
    def __init__(self,service,host):
        self.service=service;self.host=host or {}
        private=self.host.get('private_root')
        self.private_root=Path(private) if private else Path(service.temporary_root or tempfile.gettempdir())/'pptx-host-review'
        self.private_root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.ledger=vision.ReviewLedger(self.private_root/'vision-reviews.sqlite')
        self.executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='pptx-vision')
        self.inflight=set();self.lock=threading.Lock();self.closed=False
        self.timeout=float(self.host.get('vision_timeout',vision.DEFAULT_TIMEOUT))
    def store(self,path):
        # Host renders go to the private root; the sandbox cannot swap the screenshot.
        return PresentationStore(path,temporary_root=self.private_root/'renders')
    def close(self):
        self.closed=True;self.executor.shutdown(wait=False,cancel_futures=True)
    def schedule(self,key,function,*args):
        with self.lock:
            if self.closed or key in self.inflight:return False
            self.inflight.add(key)
        def run():
            try:function(*args)
            except Exception as exc:LOG.error('PPTX vision job failed: %s %s',key[0],type(exc).__name__)
            finally:
                with self.lock:self.inflight.discard(key)
                try:self.service.sync()
                except Exception as exc:LOG.error('PPTX sync after vision job failed: %s',type(exc).__name__)
        self.executor.submit(run);return True
    def observe(self,path,event):
        kind=event.get('type')
        if kind in ('presentation_review','presentation_review_requested'):
            review=event.get('review') or {}
            if review.get('automatic_passed') and review.get('rendered') and (review.get('aesthetic_review') or {}).get('status')=='pending':
                self.schedule(('slide',str(path),event['slide_id'],review['revision'],review.get('fingerprint')),self.review_slide,path,event['slide_id'],review['revision'],review.get('fingerprint'))
        elif kind=='presentation_deck_review_requested':
            deck=event.get('deck_review') or {}
            if deck.get('status')=='pending':self.schedule(('deck',str(path),deck['request_id']),self.review_deck,path,deck['request_id'],deck['revision'])
        elif kind=='presentation_finalized':
            self.schedule(('verify',str(path),event.get('revision')),self.verify_finalized,path,event)
    def resume(self,path):
        """After a restart, pick up reviews that were requested but never answered."""
        try:store=self.store(path)
        except ValueError:return
        with store.locked() as(c,state):
            if not state:return
            pending=[(sid,review) for sid,review in state.get('reviews',{}).items() if review.get('automatic_passed') and review.get('rendered') and (review.get('aesthetic_review') or {}).get('status')=='pending']
            deck=state.get('deck_review') or {}
        for sid,review in pending:self.schedule(('slide',str(path),sid,review['revision'],review.get('fingerprint')),self.review_slide,path,sid,review['revision'],review.get('fingerprint'))
        if deck.get('status')=='pending':self.schedule(('deck',str(path),deck['request_id']),self.review_deck,path,deck['request_id'],deck['revision'])
    def model(self):
        resolver=self.host.get('vision')
        if resolver is None:return None,'vision review is not configured on this host'
        try:model_id,chat=resolver()
        except Exception as exc:return None,'vision model could not be prepared: '+type(exc).__name__
        if model_id is None:return None,chat
        return vision.ModelClient(model_id,vision.langchain_invoker(chat) if hasattr(chat,'invoke') else chat,self.timeout),None
    def render(self,store,revision,data):
        with tempfile.TemporaryDirectory(prefix='pptx-host-check-') as folder:
            candidate=Path(folder)/'candidate.pptx';candidate.write_bytes(data);validate_embedded_resources(candidate)
        return engine.reviewer().render_snapshot(store,revision)
    def snapshot(self,store,revision):
        with store.locked() as(c,state):
            data=store.snapshot_unlocked(c,revision)
            recorded=json.loads(c.execute('SELECT result FROM revisions WHERE revision=?',(revision,)).fetchone()[0]).get('sha256')
            manifest=engine.renderer.manifest(engine.Presentation(io.BytesIO(data)),state)
            return data,recorded,manifest,json.loads(json.dumps(state))
    def review_slide(self,path,slide_id,check_revision,fingerprint):
        store=self.store(path)
        with store.locked() as(c,state):
            review=(state or {}).get('reviews',{}).get(slide_id)
            if not review or review.get('revision')!=check_revision or (review.get('aesthetic_review') or {}).get('status')!='pending':return
        data,recorded,manifest,state=self.snapshot(store,check_revision)
        slides=manifest['slides'];index=next((i for i,s in enumerate(slides) if s['slide_id']==slide_id),None)
        started=time.time();model_id=None;png=b'';info={};response_hash=None
        try:
            if index is None:raise vision.VisionUnavailable('image_missing','slide is not part of the checked revision')
            if hashlib.sha256(data).hexdigest()!=recorded:raise vision.VisionUnavailable('snapshot_mismatch','revision bytes do not match their recorded hash')
            try:directory=self.render(store,check_revision,data)
            except (OSError,RuntimeError,ValueError) as exc:raise vision.VisionUnavailable('render_failed',type(exc).__name__) from exc
            image=directory/f'slide-{index+1:03d}.png'
            png=image.read_bytes() if image.is_file() else b''
            info=vision.png_info(png)
            client,reason=self.model()
            if client is None:raise vision.VisionUnavailable('no_vision_model',reason)
            model_id=client.model_id
            context=dict(slide=slides[index],index=index,total=len(slides),profile=state.get('profile'),intent=review.get('intent'),
                constraints=review.get('constraints'),technical_issues=review.get('layout_issues'))
            text=client.complete(vision.SLIDE_SYSTEM_PROMPT,vision.slide_request(context),[('image/png',png)])
            response_hash=hashlib.sha256(text.encode('utf-8','replace')).hexdigest()
            result=vision.evaluate_slide(text,slides[index])
        except vision.VisionUnavailable as exc:
            result=vision.failure(exc.code,str(exc))
        for issue in result.get('issues',[]):issue['fingerprint']=vision.issue_fingerprint(issue)
        render_hash=hashlib.sha256(png).hexdigest() if png else None
        review_id=self.ledger.record(kind='slide',presentation_id=state.get('presentation_id'),path=str(path),slide_id=slide_id,revision=check_revision,
            render_sha256=render_hash,pixel_sha256=info.get('pixel_sha256'),snapshot_sha256=recorded,model_id=model_id,prompt_version=vision.PROMPT_VERSION,
            status=result['status'],summary=result.get('summary'),issues=result.get('issues'),checks=result.get('checks'),flags=result.get('flags'),
            error=json.dumps(result.get('error')) if result.get('error') else None,applied=0,response_sha256=response_hash)
        payload=dict(result,review_id=review_id,model_id=model_id,prompt_version=vision.PROMPT_VERSION,render_sha256=render_hash,
            pixel_sha256=info.get('pixel_sha256'),presentation_id=state.get('presentation_id'),slide_id=slide_id,reviewed_at=time.time(),
            duration_s=round(time.time()-started,2))
        applied=store.record_aesthetic_review(slide_id,check_revision,fingerprint,payload)
        self.ledger.mark_applied(review_id,applied.get('applied'))
    def review_deck(self,path,request_id,revision):
        store=self.store(path)
        data,recorded,manifest,state=self.snapshot(store,revision)
        deck=state.get('deck_review') or {}
        if deck.get('request_id')!=request_id or deck.get('status')!='pending':return
        slides=manifest['slides'];model_id=None;sheet=b'';response_hash=None
        try:
            if hashlib.sha256(data).hexdigest()!=recorded:raise vision.VisionUnavailable('snapshot_mismatch','revision bytes do not match their recorded hash')
            try:directory=self.render(store,revision,data)
            except (OSError,RuntimeError,ValueError) as exc:raise vision.VisionUnavailable('render_failed',type(exc).__name__) from exc
            images=[(directory/f'slide-{index+1:03d}.png').read_bytes() for index in range(len(slides))]
            sheet=vision.contact_sheet(images)
            client,reason=self.model()
            if client is None:raise vision.VisionUnavailable('no_vision_model',reason)
            model_id=client.model_id
            summary=[dict(page=index+1,slide_id=slide['slide_id'],layout=slide.get('layout'),title=_title(slide),
                elements={kind:sum(1 for e in slide['elements'] if e.get('type')==kind) for kind in {e.get('type') for e in slide['elements']}},
                aesthetic=(state.get('reviews',{}).get(slide['slide_id'],{}).get('aesthetic_review') or {}).get('status')) for index,slide in enumerate(slides)]
            text=client.complete(vision.DECK_SYSTEM_PROMPT,vision.deck_request(dict(slides=summary,profile=state.get('profile'))),[('image/png',sheet)])
            response_hash=hashlib.sha256(text.encode('utf-8','replace')).hexdigest()
            result=vision.evaluate_deck(text,len(slides))
            for issue in result['issues']+result.get('rejected_issues',[]):
                issue['slide_ids']=[slides[page-1]['slide_id'] for page in issue['slides'] if 0<page<=len(slides)]
        except vision.VisionUnavailable as exc:
            result=vision.failure(exc.code,str(exc))
        review_id=self.ledger.record(kind='deck',presentation_id=state.get('presentation_id'),path=str(path),slide_id=None,revision=revision,
            render_sha256=hashlib.sha256(sheet).hexdigest() if sheet else None,snapshot_sha256=recorded,model_id=model_id,
            prompt_version=vision.DECK_PROMPT_VERSION,status=result['status'],summary=result.get('summary'),issues=result.get('issues'),
            checks=result.get('checks'),flags=result.get('flags'),error=json.dumps(result.get('error')) if result.get('error') else None,
            applied=0,response_sha256=response_hash)
        payload=dict(result,review_id=review_id,model_id=model_id,prompt_version=vision.DECK_PROMPT_VERSION,reviewed_at=time.time())
        applied=store.record_deck_review(request_id,payload)
        self.ledger.mark_applied(review_id,applied.get('applied'))
    def verify_finalized(self,path,event):
        """Finalization written by the sandbox is only trusted if the host ledger agrees."""
        store=self.store(path);revision=event.get('revision');problems=[]
        data,recorded,manifest,state=self.snapshot(store,revision)
        pixels={}
        try:
            directory=self.render(store,revision,data)
            for index,slide in enumerate(manifest['slides']):
                image=directory/f'slide-{index+1:03d}.png'
                if image.is_file():pixels[slide['slide_id']]=vision.png_info(image.read_bytes())['pixel_sha256']
        except (OSError,RuntimeError,ValueError,vision.VisionUnavailable):problems.append('host render unavailable')
        for slide in manifest['slides']:
            sid=slide['slide_id'];accepted=(event.get('acceptance') or {}).get(sid) or {}
            decision=self.ledger.decision(accepted['decision_id']) if accepted.get('decision_id') else None
            if decision:
                if decision['decision'] not in ('accept','dismiss_issues') or decision['slide_id']!=sid:problems.append(f'{sid}: human decision does not accept this slide')
                continue
            record=self.ledger.get(accepted.get('review_id')) if accepted.get('review_id') else None
            if not record or record['status']!='passed' or record['slide_id']!=sid or record['presentation_id']!=state.get('presentation_id'):
                problems.append(f'{sid}: no host-recorded passing vision review');continue
            if pixels.get(sid) and record.get('pixel_sha256')!=pixels[sid]:problems.append(f'{sid}: reviewed screenshot differs from the finalized slide')
        self.service.emit(dict(type='presentation_finalize_verification',presentation_id=state.get('presentation_id'),revision=revision,
            path=Path(path).relative_to(self.service.root).as_posix(),finalize_verified=not problems,verification_problems=problems))


class Presentations:
    def __init__(self,root,emit,temporary_root=None,host=None):
        self.root=Path(root).resolve(); self.temporary_root=temporary_root; self.emit=emit; self.cursors={}; self.bound_paths={}; self.lock=threading.RLock()
        self.reviews=ReviewCoordinator(self,host)
    def store(self, path):
        return PresentationStore(path, temporary_root=self.temporary_root)
    def bind_state(self,record):
        self.bound_paths={identity:item['path'] for identity,item in record.get('presentations',{}).items()}
        self.cursors=dict(record.get('artifact_cursors',{}).get('pptx',{}))
        for path in self.paths():
            if path.relative_to(self.root).as_posix() not in self.cursors and path.relative_to(self.root).as_posix() not in self.bound_paths.values():
                if (path.parent/'.pptx-state'/path.name/'history.sqlite').exists():
                    events=self.store(path).events()
                    self.cursors[path.relative_to(self.root).as_posix()]=events[-1]['store_seq'] if events else 0
        for path in self.paths():
            if (path.parent/'.pptx-state'/path.name/'history.sqlite').exists():self.reviews.resume(path)
    def paths(self):
        return [p for p in self.root.rglob('*.pptx') if not p.is_symlink() and '.pptx-state' not in p.relative_to(self.root).parts and p.resolve().is_relative_to(self.root.resolve())]
    def presentation_path(self,identity):
        if identity not in self.bound_paths:raise ValueError('Unknown presentation')
        path=self.root/self.bound_paths[identity]
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):raise ValueError('Invalid presentation path')
        return path
    def close(self):self.reviews.close()
    def sync(self):
        with self.lock:
            for path in self.paths():
                if path.is_symlink() or not (path.parent/'.pptx-state'/path.name/'history.sqlite').exists():continue
                for event in self.store(path).events(self.cursors.get(path.relative_to(self.root).as_posix(),0)):
                    event['path']=path.relative_to(self.root).as_posix()
                    identity=event.get('presentation_id')
                    if identity in self.bound_paths and self.bound_paths[identity]!=event['path']:
                        self.cursors[event['path']]=event['store_seq']
                        continue
                    if identity:self.bound_paths[identity]=event['path']
                    self.emit(event); self.cursors[path.relative_to(self.root).as_posix()]=event['store_seq']
                    self.reviews.observe(path,event)
    def detect_external(self):
        # Detect unversioned native writes without replacing the last valid snapshot.
        for path in self.paths():
            if path.is_symlink():continue
            store=self.store(path)
            if not (store.directory/'history.sqlite').exists():
                relative = path.relative_to(self.root).as_posix()
                try:
                    validate_embedded_resources(path)
                    try: identity = engine.identifier(path.stem) if len(Path(relative).parts) == 1 else 'external-' + hashlib.sha256(relative.encode()).hexdigest()[:20]
                    except ValueError: identity = 'external-' + hashlib.sha256(relative.encode()).hexdigest()[:20]
                    if identity in self.bound_paths and self.bound_paths[identity] != relative:
                        identity = 'external-' + hashlib.sha256(relative.encode()).hexdigest()[:20]
                    store.track_external(identity)
                except (ValueError, OSError):
                    continue
            with store.locked() as(c,state):
                if state and hashlib.sha256(path.read_bytes()).hexdigest()!=state['sha256']:
                    self.emit(dict(type='presentation_error',presentation_id=state['presentation_id'],revision=state['revision'],path=path.name,external_conflict=True))
        self.sync()
    def feedback(self,text,context):
        identity=context.get('presentation_id') or context.get('id'); sid=context.get('slide_id'); revision=context.get('revision')
        state=self.store(self.presentation_path(identity)).status()
        if type(revision)is not int or revision>state['revision'] or sid not in [s['slide_id'] for s in state['manifest']['slides']]:raise ValueError('Invalid presentation/slide feedback version')
        return text+f'\n[Presentation feedback: presentation_id={identity}, slide_id={sid}, viewed_revision={revision}, current_revision={state["revision"]}. Prioritize this slide, invalidate affected checks, recheck before continuing.]'
    def human_decision(self,identity,revision,decision,notes,slide_id=None,issue_ids=()):
        """A user decision in the preview: ledgered on the host, then applied to the store."""
        store=self.store(self.presentation_path(identity))
        with store.locked() as(c,state):
            target=(state.get('reviews',{}).get(slide_id) or {}).get('aesthetic_review') if slide_id else state.get('deck_review')
        decision_id=self.reviews.ledger.decide(review_id=(target or {}).get('review_id'),presentation_id=identity,slide_id=slide_id,
            revision=revision,decision=decision,issue_ids=list(issue_ids),notes=notes,actor='human')
        result=store.record_human_decision(revision,decision,notes,decision_id,slide_id,tuple(issue_ids))
        self.sync();return result

def create_service(root,emit,temporary_root=None,host=None):return Presentations(root,emit,temporary_root=temporary_root,host=host)


def create_router(host):
    router=APIRouter(prefix='/api/agent/sessions')
    def store_for(session_id,presentation_id):
        service=host.service(session_id,'pptx'); record=host.session(session_id)
        if presentation_id not in record.get('presentations',{}):raise HTTPException(404,'Presentation does not belong to this session')
        try:return service,service.store(service.presentation_path(presentation_id))
        except ValueError:raise HTTPException(422,'Invalid presentation ID')
    @router.get('/{session_id}/presentations')
    def presentations(session_id:str):
        service=host.service(session_id,'pptx'); service.detect_external(); record=host.session(session_id); result=[]
        for identity in record.get('presentations',{}):
            try:result.append({**service.store(service.presentation_path(identity)).status(),'path':record['presentations'][identity]['path'],
                **{key:record['presentations'][identity][key] for key in ('finalize_verified','verification_problems') if key in record['presentations'][identity]}})
            except (ValueError,OSError):continue
        return result
    @router.post('/{session_id}/presentations/{presentation_id}/preview')
    def preview(session_id:str,presentation_id:str):
        service,store=store_for(session_id,presentation_id)
        validate_embedded_resources(store.path)
        try:result=store.preview()
        except (OSError,RuntimeError,ValueError) as exc:raise HTTPException(409,'演示文稿预览暂不可用，请检查 LibreOffice') from exc
        service.sync()
        return {**result, 'path': store.path.relative_to(service.root).as_posix()}
    @router.get('/{session_id}/presentations/{presentation_id}/revisions/{revision}')
    def snapshot(session_id:str,presentation_id:str,revision:int):
        _,store=store_for(session_id,presentation_id)
        try:data=store.snapshot(revision)
        except ValueError:raise HTTPException(404,'Unknown revision')
        return Response(data,media_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',headers={'Content-Disposition':f'attachment; filename="presentation-r{revision}.pptx"','Cache-Control':'private, max-age=31536000, immutable'})
    @router.get('/{session_id}/presentations/{presentation_id}/renders/{revision}/{filename}')
    def rendered(session_id:str,presentation_id:str,revision:int,filename:str):
        import re
        _,store=store_for(session_id,presentation_id)
        if revision<0 or not re.fullmatch(r'slide-\d{3}\.png',filename):raise HTTPException(404,'Unknown rendered page')
        path=store.render_directory(revision)/filename
        if not path.is_file():raise HTTPException(404,'Render unavailable')
        return FileResponse(path,headers={'Cache-Control':'private, max-age=31536000, immutable'})
    @router.get('/{session_id}/presentations/{presentation_id}/assets/{revision}/{slide_id}/{element_id}')
    def asset(session_id:str,presentation_id:str,revision:int,slide_id:str,element_id:str):
        _,store=store_for(session_id,presentation_id)
        try:
            prs=engine.Presentation(io.BytesIO(store.snapshot(revision))); shape=engine.find_element(engine.find_slide(prs,slide_id),element_id)
            return Response(shape.image.blob,media_type=shape.image.content_type,headers={'Cache-Control':'private, max-age=31536000, immutable'})
        except (ValueError,AttributeError):raise HTTPException(404,'Image unavailable')
    class VisualReview(BaseModel):
        slide_id:str
        revision:int=Field(ge=0)
        passed:bool
        notes:str=Field(default='',max_length=2000)
    @router.post('/{session_id}/presentations/{presentation_id}/review')
    def review(session_id:str,presentation_id:str,value:VisualReview):
        service,store=store_for(session_id,presentation_id)
        try:result=service.human_decision(presentation_id,value.revision,'accept' if value.passed else 'reject',value.notes,value.slide_id)
        except ValueError as exc:raise HTTPException(409,str(exc))
        return {**result,'path':store.path.relative_to(service.root).as_posix()}
    class AestheticDecision(BaseModel):
        model_config={'extra':'forbid'}
        slide_id:str|None=None
        revision:int=Field(ge=0)
        decision:Literal['accept','reject','dismiss_issues']
        issue_ids:list[str]=Field(default_factory=list,max_length=12)
        notes:str=Field(default='',max_length=2000)
    @router.post('/{session_id}/presentations/{presentation_id}/aesthetic-decision')
    def aesthetic_decision(session_id:str,presentation_id:str,value:AestheticDecision):
        service,store=store_for(session_id,presentation_id)
        try:result=service.human_decision(presentation_id,value.revision,value.decision,value.notes,value.slide_id,value.issue_ids)
        except ValueError as exc:raise HTTPException(409,str(exc))
        return {**result,'path':store.path.relative_to(service.root).as_posix()}
    class ReviewRequest(BaseModel):
        model_config={'extra':'forbid'}
        slide_id:str|None=None
        revision:int=Field(ge=0)
    @router.post('/{session_id}/presentations/{presentation_id}/aesthetic-review')
    def request_review(session_id:str,presentation_id:str,value:ReviewRequest):
        service,store=store_for(session_id,presentation_id)
        try:result=store.host_request_review(value.slide_id,value.revision) if value.slide_id else store.request_deck_review(value.revision)
        except ValueError as exc:raise HTTPException(409,str(exc))
        service.sync()
        return {**result,'path':store.path.relative_to(service.root).as_posix()}
    return router
