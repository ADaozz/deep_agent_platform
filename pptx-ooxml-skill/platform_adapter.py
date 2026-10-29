"""Uninstallable PPTX feature: tool provider and presentation HTTP API."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import threading
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response,FileResponse
from pydantic import BaseModel,Field
from platform_app import domain as d

spec=importlib.util.spec_from_file_location('pptx_feature_engine',Path(__file__).parent/'scripts'/'office_pptx.py')
engine=importlib.util.module_from_spec(spec); spec.loader.exec_module(engine)
PresentationStore=engine.PresentationStore
APPROVAL_TOOLS=('pptx_init',)
INSTRUCTIONS='''PPTX requests use the optional PPTX feature, not create_pptx or Python scripts writing a complete deck. Inspect/reuse uploaded templates first. Call pptx_init; its one native approval grants only this presentation in this session. Each pptx_apply_one call handles one slide or one native element; never whole-deck generation. Use stable IDs, operation_id and optimistic base_revision. Choose one of cover, section, title_body, comparison, three_columns, data before adding elements; use layout anchors and theme tokens. Native text/shape/image/table/chart remain editable. Complete a slide with pptx_check_slide and concrete content_notes. Automatic failures block the next slide, allow at most 3 failed checks then restore/request intervention. Normal mode keeps actual visual review pending, strict mode requires it before next slide. Actually inspect the Impress PNG before pptx_review; text-only models must ask for visual inspection. Recheck all affected slides after theme/layout changes. Steering binds viewed slide and revision; fix affected historical slide before proceeding. Do not claim completion before pptx_finalize accepts every slide. User authorization never extends to other files or generic execute/network tools.'''


def safe_path(root,presentation_id):
    engine.identifier(presentation_id); root=Path(root).resolve(); path=root/(presentation_id+'.pptx')
    if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('Invalid presentation path')
    return path


class Presentations:
    approval_tools=('pptx_init',)
    def __init__(self,root,workspace_id,emit,actor,vision=False):
        self.root=Path(root); self.workspace_id=workspace_id; self.emit=emit; self.actor=actor; self.vision=vision; self.cursors={}; self.lock=threading.RLock()
    def bind_state(self,record):
        self.cursors=dict(record.get('artifact_cursors',{}).get('pptx',{}))
        for path in self.root.glob('*.pptx'):
            if path.name not in self.cursors and path.stem not in record.get('presentations',{}):
                if (path.parent/'.pptx-state'/path.name/'history.sqlite').exists():
                    events=PresentationStore(path).events()
                    self.cursors[path.name]=events[-1]['store_seq'] if events else 0
    def close(self):pass
    def sync(self):
        with self.lock:
            for path in self.root.glob('*.pptx'):
                if path.is_symlink() or not (path.parent/'.pptx-state'/path.name/'history.sqlite').exists():continue
                for event in PresentationStore(path).events(self.cursors.get(path.name,0)):
                    event['workspace_id']=self.workspace_id
                    self.emit(event); self.cursors[path.name]=event['store_seq']
    def detect_external(self):
        # Detect unversioned native writes without replacing the last valid snapshot.
        for path in self.root.glob('*.pptx'):
            if path.is_symlink():continue
            store=PresentationStore(path)
            if not (store.directory/'history.sqlite').exists():continue
            with store.locked() as(c,state):
                if state and hashlib.sha256(path.read_bytes()).hexdigest()!=state['sha256']:
                    self.emit(dict(type='presentation_error',presentation_id=state['presentation_id'],revision=state['revision'],path=path.name,external_conflict=True))
        self.sync()
    def feedback(self,text,context):
        identity=context.get('presentation_id') or context.get('id'); sid=context.get('slide_id'); revision=context.get('revision')
        state=PresentationStore(safe_path(self.root,identity)).status()
        if type(revision)is not int or revision>state['revision'] or sid not in [s['slide_id'] for s in state['manifest']['slides']]:raise ValueError('Invalid presentation/slide feedback version')
        return text+f'\n[Presentation feedback: presentation_id={identity}, slide_id={sid}, viewed_revision={revision}, current_revision={state["revision"]}. Prioritize this slide, invalidate affected checks, recheck before continuing.]'
    def execute(self,action,args):
        self.root.mkdir(parents=True,exist_ok=True); identity=args['presentation_id']; store=PresentationStore(safe_path(self.root,identity))
        grant_key=self.actor+':'+identity
        if action!='init':
            try:grant=d.get('artifact_grant',grant_key)
            except KeyError:raise ValueError('Approve pptx_init once for this presentation and session first')
            if grant.get('path')!=str(store.path):raise ValueError('Presentation scope changed; authorization required')
        try:
            if action=='init':
                template=args.get('template')
                if template:
                    template=(self.root/template).resolve()
                    if not template.is_relative_to(self.root.resolve()) or not template.is_file():raise ValueError('Template must be a workspace file')
                result=store.init(identity,template,args.get('mode','normal'),args.get('theme','corporate_blue'))
                d.put('artifact_grant',dict(id=grant_key,path=str(store.path),actor=self.actor,presentation_id=identity,scope=['init','apply_one','status','check_slide','review','restore','finalize']))
                return result
            if action=='apply_one':return store.apply_one({k:v for k,v in args.items() if k!='action'},self.root)
            if action=='status':return store.status()
            if action=='check_slide':return store.check_slide(args['slide_id'],args['content_notes'])
            if action=='review':
                if not self.vision:raise ValueError('Text-only model cannot certify visual review')
                return store.review(args['slide_id'],args['revision'],args['passed'],args['notes'])
            if action=='restore':return store.restore(args['revision'],args['base_revision'],args['operation_id'])
            if action=='finalize':return store.finalize(args['base_revision'])
            raise ValueError('Unsupported feature action')
        finally:self.sync()
    def tools(self):
        from langchain_core.tools import StructuredTool
        text=dict(type='string'); integer=dict(type='integer',minimum=0)
        def schema(properties,required=()):return dict(type='object',properties=dict(presentation_id=text,**properties),required=['presentation_id',*required],additionalProperties=False)
        definitions={
         'init':(schema(dict(template=text,theme=dict(type='string',enum=list(engine.THEMES)),mode=dict(type='string',enum=['normal','strict']))),'Initialize an optional-template presentation. Native approval grants only this deck/session.'),
         'apply_one':(schema(dict(operation_id=text,base_revision=integer,slide_id=text,operation=dict(type='string',enum=['slide.add','slide.set_layout','slide.reorder','presentation.set_theme','element.add_text','element.add_shape','element.add_image','element.add_table','element.add_chart','element.update','element.remove']),element=dict(type='object'),layout=dict(type='string',enum=list(engine.LAYOUTS)),layout_index=integer,theme=text,index=integer),['operation_id','base_revision','operation']),'Commit one native element or slide operation. element requires id, normalized geometry{x,y,w,h} or layout anchor; text style token cover_title/slide_title/body/caption/card_title. Shapes rectangle/rounded_rectangle/arrow/ellipse, optional fill_token and explicit layer background/decoration/content or overlap_group. Table rows<=10x8. Chart {type:bar|line|pie,categories,series:[{name,values}]}. No batching or whole-deck content.'),
         'status':(schema({}),'Inspect stable slide/element IDs, geometry, theme, versions and review state.'),
         'check_slide':(schema(dict(slide_id=text,content_notes=text),['slide_id','content_notes']),'Formally render exact snapshot with Impress and inspect structure/layout. Supply actual content-review notes. Visual inspection remains pending.'),
         'review':(schema(dict(slide_id=text,revision=integer,passed=dict(type='boolean'),notes=text),['slide_id','revision','passed','notes']),'Record actual screenshot inspection. Requires a vision-capable model and concrete evidence.'),
         'restore':(schema(dict(revision=integer,base_revision=integer,operation_id=text),['revision','base_revision','operation_id']),'Restore known snapshot as a new monotonically increasing revision.'),
         'finalize':(schema(dict(base_revision=integer),['base_revision']),'Publish after every slide passes actual content and visual review.')}
        result=[]
        for action,(args,description) in definitions.items():
            def invoke(_action=action,**kwargs):
                try:
                    value=self.execute(_action,kwargs)
                    if _action in ('apply_one','init','restore'):
                        return {key:item for key,item in value.items() if key not in ('manifest','history')}
                    return value
                except (ValueError,KeyError,OSError,TypeError) as exc:return dict(status='rejected',error=str(exc))
            result.append(StructuredTool.from_function(func=invoke,name='pptx_'+action,description=description,args_schema=args))
        return result


def create_service(root,workspace_id,emit,actor,vision=False):return Presentations(root,workspace_id,emit,actor,vision)


def create_router(host):
    router=APIRouter(prefix='/api/agent/sessions')
    def store_for(session_id,presentation_id):
        service=host.service(session_id,'pptx'); record=host.session(session_id)
        if presentation_id not in record.get('presentations',{}):raise HTTPException(404,'Presentation does not belong to this session')
        try:return service,PresentationStore(safe_path(service.root,presentation_id))
        except ValueError:raise HTTPException(422,'Invalid presentation ID')
    @router.get('/{session_id}/presentations')
    def presentations(session_id:str):
        service=host.service(session_id,'pptx'); service.sync(); record=host.session(session_id); result=[]
        for identity in record.get('presentations',{}):
            try:result.append(PresentationStore(safe_path(service.root,identity)).status())
            except (ValueError,OSError):continue
        return result
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
        path=store.directory/f'render-{revision:08d}'/filename
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
        service.sync(); return result
    return router
