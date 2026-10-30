"""Read-only PPTX indexing, preview and human review HTTP API."""
import hashlib
import importlib.util
import io
from pathlib import Path
import threading
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response,FileResponse
from pydantic import BaseModel,Field
from platform_app.artifact_runtime import validate_embedded_resources

spec=importlib.util.spec_from_file_location('pptx_feature_engine',Path(__file__).parent/'scripts'/'office_pptx.py')
engine=importlib.util.module_from_spec(spec); spec.loader.exec_module(engine)
PresentationStore=engine.PresentationStore


class Presentations:
    def __init__(self,root,emit,temporary_root=None):
        self.root=Path(root).resolve(); self.temporary_root=temporary_root; self.emit=emit; self.cursors={}; self.bound_paths={}; self.lock=threading.RLock()
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
    def paths(self):
        return [p for p in self.root.rglob('*.pptx') if not p.is_symlink() and '.pptx-state' not in p.relative_to(self.root).parts and p.resolve().is_relative_to(self.root.resolve())]
    def presentation_path(self,identity):
        if identity not in self.bound_paths:raise ValueError('Unknown presentation')
        path=self.root/self.bound_paths[identity]
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):raise ValueError('Invalid presentation path')
        return path
    def close(self):pass
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

def create_service(root,emit,temporary_root=None):return Presentations(root,emit,temporary_root=temporary_root)


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
            try:result.append({**service.store(service.presentation_path(identity)).status(),'path':record['presentations'][identity]['path']})
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
        notes:str=Field(min_length=1,max_length=2000)
    @router.post('/{session_id}/presentations/{presentation_id}/review')
    def review(session_id:str,presentation_id:str,value:VisualReview):
        service,store=store_for(session_id,presentation_id)
        try:result=store.review(value.slide_id,value.revision,value.passed,value.notes,'human')
        except ValueError as exc:raise HTTPException(409,str(exc))
        service.sync(); return {**result,'path':store.path.relative_to(service.root).as_posix()}
    return router
