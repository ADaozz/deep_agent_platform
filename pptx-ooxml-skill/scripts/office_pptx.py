#!/usr/bin/env python3
"""Element-atomic PPTX engine, using the platform's shared artifact transaction store."""
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import time

from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE,MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE,XL_LEGEND_POSITION
from pptx.oxml.xmlchemy import OxmlElement


def load(name):
    path=Path(__file__).with_name(name+'.py')
    if name=='artifact_runtime' and not path.exists():path=Path(__file__).resolve().parents[2]/'platform_app'/'artifact_runtime.py'
    spec=importlib.util.spec_from_file_location(name,path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

shared=load('artifact_runtime'); renderer=load('slide_renderer'); validator=load('slide_validator')
THEMES={
 'corporate_blue':dict(background='FFFFFF',text='17324D',accent='246BCE',muted='536B82',card='EDF4FC'),
 'slate_dark':dict(background='14263D',text='FFFFFF',accent='66B5FF',muted='BCD0E4',card='203B58')}
STYLES={'slide_title':dict(font_size=30,bold=True,color_token='text'),'cover_title':dict(font_size=40,bold=True,color_token='text'),
 'body':dict(font_size=20,bold=False,color_token='text'),'caption':dict(font_size=12,bold=False,color_token='muted'),'card_title':dict(font_size=24,bold=True,color_token='accent')}
LAYOUTS={
 'cover':dict(title=(.08,.28,.84,.19),subtitle=(.08,.52,.8,.14),footer=(.08,.88,.8,.05)),
 'section':dict(title=(.08,.32,.84,.2),subtitle=(.08,.58,.8,.14),footer=(.08,.88,.8,.05)),
 'title_body':dict(title=(.06,.06,.88,.14),body=(.08,.26,.84,.57),footer=(.06,.92,.88,.04)),
 'comparison':dict(title=(.06,.06,.88,.14),left=(.06,.27,.42,.54),right=(.52,.27,.42,.54),footer=(.06,.92,.88,.04)),
 'three_columns':dict(title=(.06,.06,.88,.14),column1=(.06,.28,.26,.52),column2=(.37,.28,.26,.52),column3=(.68,.28,.26,.52),footer=(.06,.92,.88,.04)),
 'data':dict(title=(.06,.06,.88,.14),chart=(.07,.26,.58,.57),note=(.7,.29,.24,.5),footer=(.06,.92,.88,.04))}


def identifier(value):return shared.identifier(value)


def resolve_style(state,element):
    token=element.get('style','body')
    if token not in STYLES:raise ValueError('Unknown theme text token')
    base=STYLES[token]; palette=state['theme']
    return dict(font_family=state['font_family'],font_size=base['font_size'],bold=base['bold'],color=palette[base['color_token']])


def geometry(element,layout,slots=None):
    if element.get('anchor'):
        anchors={**LAYOUTS[layout],**(slots or {})}
        if element['anchor'] not in anchors:raise ValueError('Unknown layout anchor')
        g=dict(zip(('x','y','w','h'),anchors[element['anchor']]))
    else:g=element.get('geometry',{})
    if set(g)!=set(('x','y','w','h')) or any(type(v) not in (int,float) or not math.isfinite(v) for v in g.values()):raise ValueError('Normalized finite geometry required')
    if min(g['x'],g['y'])<0 or min(g['w'],g['h'])<=0 or g['x']+g['w']>1.00001 or g['y']+g['h']>1.00001:raise ValueError('Element outside slide canvas')
    return g


def apply_text(shape,text,style):
    if not isinstance(text,str) or len(text)>1200:raise ValueError('One bounded text element, maximum 1200 characters')
    frame=shape.text_frame; frame.clear(); frame.word_wrap=True
    frame.margin_left=frame.margin_right=Pt(3); frame.margin_top=frame.margin_bottom=Pt(2)
    for i,line in enumerate(text.split('\n')):
        p=frame.paragraphs[0] if i==0 else frame.add_paragraph(); p.text=line; p.space_after=Pt(4)
        for run in p.runs:
            run.font.name=style['font_family']; run.font.size=Pt(style['font_size']); run.font.bold=style['bold']; run.font.color.rgb=RGBColor.from_string(style['color'])
            rpr=run._r.get_or_add_rPr(); east=OxmlElement('a:ea'); east.set('typeface',style['font_family']); rpr.append(east)


def chart_data(chart):
    categories=chart['categories'];series=chart['series']
    if not 1<=len(categories)<=12 or any(not isinstance(label,str) or len(label)>80 for label in categories) or not 1<=len(series)<=4:
        raise ValueError('Bounded chart categories/series required')
    values=CategoryChartData();values.categories=categories
    for item in series:
        if not isinstance(item.get('name'),str) or len(item['name'])>80 or len(item['values'])!=len(categories) or any(type(n)not in (int,float) or not math.isfinite(n) for n in item['values']):
            raise ValueError('Bounded finite chart series required')
        values.add_series(item['name'],item['values'])
    return values


def serialize(prs):
    out=io.BytesIO(); prs.save(out); data=out.getvalue(); validator.validate_package(data); return data


def find_slide(prs,sid):
    values=[s for s in prs.slides if renderer.slide_identity(s)==sid]
    if len(values)!=1:raise ValueError('Unknown or ambiguous stable slide ID')
    return values[0]


def find_element(slide,eid):
    values=[s for s in slide.shapes if renderer.element_identity(s)==eid]
    if len(values)!=1:raise ValueError('Unknown or ambiguous element ID')
    return values[0]


class PresentationStore(shared.ArtifactStore):
    def __init__(self,path):super().__init__(path,'.pptx','.pptx-state')
    def assert_current(self,state,base_revision=None):
        if state is None:raise ValueError('Presentation not initialized')
        if base_revision is not None and (type(base_revision) is not int or base_revision!=state['revision']):raise ValueError('Revision conflict')
        if not self.path.exists() or hashlib.sha256(self.path.read_bytes()).hexdigest()!=state['sha256']:raise ValueError('External modification; inspect or restore the last snapshot')
    def commit(self,c,state,data,operation_id,request,changed,kind='presentation_revision'):
        validator.validate_package(data)
        state.update(revision=state['revision']+1,sha256=hashlib.sha256(data).hexdigest(),pending_publish=True,finalized=False)
        prs=Presentation(io.BytesIO(data))
        if changed==['*']:
            state['reviews']={}; state['last_reviews']={}; state['renders']={}; state['repair_attempts']={}
        else:
            for sid in changed:
                old=state.setdefault('reviews',{}).pop(sid,None)
                if old:state.setdefault('last_reviews',{})[sid]=old
        view=renderer.manifest(prs,state)
        event=dict(status='committed',presentation_id=state['presentation_id'],revision=state['revision'],sha256=state['sha256'],operation_id=operation_id,
            slide_id=request.get('slide_id') or state.get('active_slide'),changed_elements=[request.get('element',{}).get('id','*')],changed_slides=changed,
            manifest=view,path=self.path.name,theme=state['theme'],font_family=state['font_family'],committed_at=time.time())
        self.persist_revision(c,state,data,event,request,operation_id,kind)
        return event
    def init(self,presentation_id,template=None,mode='normal',theme='corporate_blue'):
        identifier(presentation_id)
        if mode not in ('normal','strict') or theme not in THEMES:raise ValueError('Unknown review mode/theme')
        with self.locked() as (c,state):
            if state:
                if state['presentation_id']!=presentation_id:raise ValueError('Presentation identity mismatch')
                return self.view(c,state)
            if self.path.exists():raise ValueError('File already exists')
            if template:
                validator.validate_package(Path(template).read_bytes()); prs=Presentation(template)
            else:
                prs=Presentation(); prs.slide_width=Inches(13.333333); prs.slide_height=Inches(7.5)
            palette=copy.deepcopy(THEMES[theme]); font='Noto Sans CJK SC'
            if template:
                from pptx.opc.constants import RELATIONSHIP_TYPE as RT
                from lxml import etree
                theme_part=prs.slide_masters[0].part.part_related_by(RT.THEME)
                xml=etree.fromstring(theme_part.blob)
                ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
                accent=xml.xpath('string(//a:clrScheme/a:accent1/a:srgbClr/@val)',namespaces=ns)
                if len(accent)==6:palette['accent']=accent
                faces=xml.xpath('//a:fontScheme/a:minorFont/a:ea/@typeface',namespaces=ns)
                if faces and faces[0]:font=faces[0]
            state=dict(presentation_id=presentation_id,revision=-1,mode=mode,theme=palette,theme_name=theme,font_family=font,
                slots={},layouts={},backgrounds={},reviews={},renders={},repair_attempts={},template=bool(template),template_complex={})
            for slide in prs.slides:
                sid=renderer.slide_identity(slide)
                state['template_complex'][sid]=bool(template)
            state['active_slide']=renderer.slide_identity(prs.slides[0]) if len(prs.slides) else None
            return self.commit(c,state,serialize(prs),'__init__',{'template':str(template)},['*'],'presentation_initialized')
    def view(self,c,state):
        if state is None:raise ValueError('Presentation not initialized')
        data=c.execute('SELECT docx FROM revisions WHERE revision=?',(state['revision'],)).fetchone()[0]
        return dict(presentation_id=state['presentation_id'],revision=state['revision'],sha256=state['sha256'],path=self.path.name,
            manifest=renderer.manifest(Presentation(io.BytesIO(data)),state),active_slide=state.get('active_slide'),finalized=state.get('finalized',False),mode=state['mode'],theme=state['theme'],
            history=[{key:value for key,value in json.loads(r[0]).items() if key in ('revision','operation_id','sha256','slide_id','status')} for r in c.execute('SELECT result FROM revisions ORDER BY revision')])
    def status(self):
        with self.locked() as (c,state):return self.view(c,state)
    def apply_one(self,request,asset_root=None):
        identifier(request['operation_id'])
        if len(json.dumps(request,ensure_ascii=False))>30000:raise ValueError('Bounded operation payload exceeded')
        with self.locked() as (c,state):
            if not state or request['presentation_id']!=state['presentation_id']:raise ValueError('Presentation identity mismatch')
            old=c.execute('SELECT request,result FROM revisions WHERE operation_id=?',(request['operation_id'],)).fetchone()
            if old:
                if json.loads(old[0])!=request:raise ValueError('Operation ID reused with different request')
                return json.loads(old[1])
            self.assert_current(state,request['base_revision']); prs=Presentation(io.BytesIO(self.snapshot_unlocked(c,state['revision'])))
            state=copy.deepcopy(state); op=request['operation']; sid=request.get('slide_id'); changed=[sid] if sid else ['*']
            if op=='slide.add':
                for slide in prs.slides:
                    review=state['reviews'].get(renderer.slide_identity(slide),{})
                    if review.get('status')=='blocked' or not review.get('automatic_passed') or (state['mode']=='strict' and review.get('visual_review')!='passed'):raise ValueError('Check all affected slides before creating the next slide')
                identifier(sid)
                if any(renderer.slide_identity(s)==sid for s in prs.slides):raise ValueError('Duplicate slide ID')
                index=request.get('layout_index',next((i for i,layout in enumerate(prs.slide_layouts) if layout.name.lower()=='blank'),0))
                if type(index) is not int or not 0<=index<len(prs.slide_layouts):raise ValueError('Unknown template layout')
                slide=prs.slides.add_slide(prs.slide_layouts[index]); slide._element.cSld.set('name','da-slide:'+sid)
                state.setdefault('slots',{})[sid]={}
                if state['template']:
                    for placeholder in slide.placeholders:
                        name='title' if placeholder.is_placeholder and placeholder.placeholder_format.type in (1,3) else 'body'
                        state['slots'][sid][name]=(placeholder.left/prs.slide_width,placeholder.top/prs.slide_height,placeholder.width/prs.slide_width,placeholder.height/prs.slide_height)
                state['active_slide']=sid
                state['layouts'][sid]=request.get('layout','title_body')
                if state['layouts'][sid] not in LAYOUTS:raise ValueError('Unknown layout intent')
                if state['template']:state['template_complex'][sid]=True
                slide.background.fill.solid(); slide.background.fill.fore_color.rgb=RGBColor.from_string(state['theme']['background']); state['backgrounds'][sid]=state['theme']['background']
            elif op=='presentation.set_theme':
                theme=request['theme']
                if theme not in THEMES:raise ValueError('Unknown theme')
                state['theme']=copy.deepcopy(THEMES[theme]); state['theme_name']=theme; changed=['*']
                for slide in prs.slides:
                    slide.background.fill.solid(); slide.background.fill.fore_color.rgb=RGBColor.from_string(state['theme']['background']); state['backgrounds'][renderer.slide_identity(slide)]=state['theme']['background']
                    for shape in slide.shapes:
                        meta=renderer.metadata(shape)
                        if meta.get('kind')=='text':
                            meta['resolved_style']=resolve_style(state,meta); apply_text(shape,meta['text'],meta['resolved_style']); renderer.set_metadata(shape,meta)
                        elif meta.get('kind')=='shape' and meta.get('fill_token'):
                            meta['fill']=state['theme'][meta['fill_token']]; shape.fill.solid(); shape.fill.fore_color.rgb=RGBColor.from_string(meta['fill']); renderer.set_metadata(shape,meta)
            elif op=='slide.reorder':
                slide=find_slide(prs,sid); index=request['index']
                if type(index)is not int or not 0<=index<len(prs.slides):raise ValueError('Invalid slide order')
                node=next(n for n in prs.slides._sldIdLst if int(n.get('id'))==slide.slide_id); prs.slides._sldIdLst.remove(node); prs.slides._sldIdLst.insert(index,node); changed=['*']
            else:
                slide=find_slide(prs,sid)
                if state.get('repair_attempts',{}).get(sid,0)>=3 and state.get('reviews',{}).get(sid,{}).get('status')=='blocked':raise ValueError('Slide repair budget exhausted; restore a known version or request review')
                if op=='slide.set_layout':
                    layout=request['layout']
                    if layout not in LAYOUTS:raise ValueError('Unknown layout')
                    state['layouts'][sid]=layout
                    for shape in slide.shapes:
                        meta=renderer.metadata(shape)
                        if meta.get('anchor') and meta['anchor'] in LAYOUTS[layout]:
                            set_geometry(shape,geometry(meta,layout),prs)
                elif op=='element.remove':
                    shape=find_element(slide,request['element']['id']); shape._element.getparent().remove(shape._element)
                elif op=='element.update':
                    element=request['element']; shape=find_element(slide,element['id']); meta=renderer.metadata(shape)
                    if set(element)-{'id','text','geometry','anchor','fill','style','layer','overlap_group','rows','chart'}:raise ValueError('Unsupported element update field')
                    if 'rows' in element:
                        rows=element['rows']
                        if not shape.has_table or len(rows)!=len(shape.table.rows) or any(len(row)!=len(shape.table.columns) or any(not isinstance(text,str) or len(text)>160 for text in row) for row in rows):
                            raise ValueError('Table updates require bounded rows with existing dimensions')
                        for r,row in enumerate(rows):
                            for col,text in enumerate(row):
                                cell=shape.table.cell(r,col);cell.text=text
                                for paragraph in cell.text_frame.paragraphs:
                                    for run in paragraph.runs:run.font.name=state['font_family'];run.font.size=Pt(14)
                        meta['rows']=rows
                    if 'chart' in element:
                        if not shape.has_chart:raise ValueError('Chart data update requires a native chart')
                        if element['chart'].get('type','bar')!=meta.get('chart',{}).get('type','bar'):raise ValueError('Changing chart type requires replacing the chart object')
                        shape.chart.replace_data(chart_data(element['chart']));meta['chart']=element['chart']
                    if element.get('geometry') or element.get('anchor'):set_geometry(shape,geometry(element,state['layouts'].get(sid,'title_body'),state.get('slots',{}).get(sid)),prs)
                    if 'text'in element:
                        if not shape.has_text_frame:raise ValueError('Text update requires a text element')
                        if not meta:meta=dict(kind='text',style=element.get('style','body'))
                        meta.update(text=element['text'],style=element.get('style',meta.get('style','body'))); meta['resolved_style']=resolve_style(state,meta); apply_text(shape,meta['text'],meta['resolved_style'])
                    if 'fill'in element:
                        color=element['fill']; check_color(color)
                        if hasattr(shape,'fill'):shape.fill.solid();shape.fill.fore_color.rgb=RGBColor.from_string(color)
                        else:shape.line.color.rgb=RGBColor.from_string(color)
                        meta['fill']=color
                    if 'style'in element and shape.has_text_frame:
                        meta['style']=element['style']; meta['resolved_style']=resolve_style(state,meta); apply_text(shape,shape.text,meta['resolved_style'])
                    meta.update({k:element[k] for k in ('anchor','layer','overlap_group') if k in element}); renderer.set_metadata(shape,meta)
                elif op.startswith('element.add_'):
                    element=request['element']; identifier(element['id'])
                    if any(renderer.element_identity(s)==element['id'] for s in slide.shapes):raise ValueError('Duplicate stable element ID')
                    g=geometry(element,state['layouts'].get(sid,'title_body'),state.get('slots',{}).get(sid)); pos=positions(g,prs); kind=op.removeprefix('element.add_')
                    meta=dict(element,kind=kind,resolved_style=resolve_style(state,element))
                    if meta.get('layer','content') not in ('content','background','decoration'):raise ValueError('Unknown layer intent')
                    if kind=='text':
                        shape=slide.shapes.add_textbox(*pos); apply_text(shape,element['text'],meta['resolved_style'])
                    elif kind=='shape' and element.get('shape')=='line':
                        shape=slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,pos[0],pos[1],pos[0]+pos[2],pos[1]+pos[3])
                        meta['shape']='line';meta['fill']=state['theme']['accent'];shape.line.color.rgb=RGBColor.from_string(meta['fill']);shape.line.width=Pt(1.5)
                    elif kind=='shape':
                        shape_type={'rectangle':MSO_AUTO_SHAPE_TYPE.RECTANGLE,'rounded_rectangle':MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,'arrow':MSO_AUTO_SHAPE_TYPE.RIGHT_ARROW,'ellipse':MSO_AUTO_SHAPE_TYPE.OVAL}.get(element.get('shape','rectangle'))
                        if shape_type is None:raise ValueError('Unsupported native shape')
                        shape=slide.shapes.add_shape(shape_type,*pos); meta['shape']=element.get('shape','rectangle'); meta['fill_token']=element.get('fill_token','card'); meta['fill']=state['theme'][meta['fill_token']]; shape.fill.solid(); shape.fill.fore_color.rgb=RGBColor.from_string(meta['fill']); shape.line.fill.background()
                    elif kind=='image':
                        asset=(Path(asset_root or self.path.parent)/element['path']).resolve(); assets=Path(asset_root or self.path.parent).resolve()
                        if not asset.is_relative_to(assets) or not asset.is_file() or asset.stat().st_size>12*1024*1024:raise ValueError('Unsafe or oversized image')
                        from PIL import Image
                        with Image.open(asset) as image:
                            if image.format not in ('PNG','JPEG') or image.width*image.height>40000000:raise ValueError('Unsupported bounded image')
                            if abs(image.width/image.height-g['w']*prs.slide_width/(g['h']*prs.slide_height))>.03:raise ValueError('Image geometry must preserve aspect ratio')
                        shape=slide.shapes.add_picture(str(asset),*pos)
                    elif kind=='table':
                        rows=element['rows']
                        if not isinstance(rows,list) or not 1<=len(rows)<=10 or not 1<=len(rows[0])<=8 or any(len(row)!=len(rows[0]) or any(not isinstance(t,str) or len(t)>160 for t in row) for row in rows):raise ValueError('Bounded uniform native table required')
                        shape=slide.shapes.add_table(len(rows),len(rows[0]),*pos)
                        for r,row in enumerate(rows):
                            for col,text in enumerate(row):
                                cell=shape.table.cell(r,col); cell.text=text
                                for paragraph in cell.text_frame.paragraphs:
                                    for run in paragraph.runs:run.font.name=state['font_family']; run.font.size=Pt(14); run.font.color.rgb=RGBColor.from_string(state['theme']['text'])
                                if r==0:cell.fill.solid(); cell.fill.fore_color.rgb=RGBColor.from_string(state['theme']['card'])
                    elif kind=='chart':
                        chart=element['chart']; categories=chart['categories']; series=chart['series']
                        if not 1<=len(categories)<=12 or any(not isinstance(label,str) or len(label)>80 for label in categories) or not 1<=len(series)<=4 or any(not isinstance(item.get('name'),str) or len(item['name'])>80 for item in series) or any(len(s['values'])!=len(categories) or any(type(n)not in (int,float) or not math.isfinite(n) for n in s['values']) for s in series):raise ValueError('Bounded finite editable chart data required')
                        chart_type={'bar':XL_CHART_TYPE.COLUMN_CLUSTERED,'line':XL_CHART_TYPE.LINE,'pie':XL_CHART_TYPE.PIE}.get(chart.get('type','bar'))
                        if chart_type is None:raise ValueError('Unsupported native chart')
                        values=CategoryChartData(); values.categories=categories
                        for item in series:values.add_series(item['name'],item['values'])
                        shape=slide.shapes.add_chart(chart_type,*pos,values); shape.chart.has_legend=True; shape.chart.legend.position=XL_LEGEND_POSITION.BOTTOM; shape.chart.legend.font.name=state['font_family']; shape.chart.legend.font.size=Pt(12)
                    else:raise ValueError('Unsupported native element type')
                    shape.name='da:'+element['id']; renderer.set_metadata(shape,meta)
                else:raise ValueError('Unsupported atomic presentation operation')
            return self.commit(c,state,serialize(prs),request['operation_id'],request,changed)
    def prepare_restore(self,c,state,revision):
        prior=json.loads(c.execute('SELECT result FROM revisions WHERE revision=?',(revision,)).fetchone()[0])
        state=copy.deepcopy(state)
        state['theme']=prior.get('theme',state['theme']);state['font_family']=prior.get('font_family',state['font_family'])
        state['layouts']={s['slide_id']:s['layout'] for s in prior['manifest']['slides']}
        state['backgrounds']={s['slide_id']:s['background'] for s in prior['manifest']['slides']}
        state['active_slide']=prior.get('slide_id')
        return state

    def snapshot_unlocked(self,c,revision):return c.execute('SELECT docx FROM revisions WHERE revision=?',(revision,)).fetchone()[0]
    def emit_state(self,c,state,event):
        self.save_state(c,state); c.execute('INSERT INTO events(data) VALUES (?)',(json.dumps(event),)); c.commit(); return event
    def check_slide(self,slide_id,content_notes):
        reviewer=load('slide_reviewer'); return reviewer.check(self,slide_id,content_notes)
    def review(self,slide_id,revision,passed,notes,reviewer='vision_model'):
        if reviewer not in ('vision_model','human') or type(passed)is not bool or not notes.strip():raise ValueError('Actual visual inspection and evidence required')
        with self.locked() as(c,state):
            self.assert_current(state,revision); review=state['reviews'].get(slide_id)
            if not review or not review['automatic_passed'] or review['revision']>revision:raise ValueError('Exact-revision successful formal check required')
            formal=state.get('renders',{}).get(slide_id)
            if not formal or not (self.directory/f"render-{formal['revision']:08d}"/formal['image']).is_file():raise ValueError('Formal screenshot is missing')
            review.update(visual_review='passed' if passed else 'failed',status='passed' if passed else 'blocked',visual_evidence=dict(reviewer=reviewer,notes=notes))
            return self.emit_state(c,state,dict(type='presentation_review',presentation_id=state['presentation_id'],revision=revision,slide_id=slide_id,manifest=renderer.manifest(Presentation(io.BytesIO(self.snapshot_unlocked(c,revision))),state),path=self.path.name))
    def finalize(self,base_revision):
        with self.locked() as(c,state):
            self.assert_current(state,base_revision); prs=Presentation(io.BytesIO(self.snapshot_unlocked(c,base_revision)))
            if not len(prs.slides):raise ValueError('Empty presentation')
            for slide in prs.slides:
                review=state['reviews'].get(renderer.slide_identity(slide),{})
                if review.get('status')!='passed' or not review.get('automatic_passed') or review.get('visual_review')!='passed' or not review.get('content_notes'):raise ValueError('Every slide requires actual content and visual acceptance')
            validator.validate_package(self.snapshot_unlocked(c,base_revision)); state['finalized']=True
            return self.emit_state(c,state,dict(type='presentation_finalized',presentation_id=state['presentation_id'],revision=base_revision,finalized=True,path=self.path.name))


def check_color(color):
    if not isinstance(color,str) or len(color)!=6 or any(c not in '0123456789abcdefABCDEF' for c in color):raise ValueError('Invalid RGB color')


def positions(g,prs):return (round(g['x']*prs.slide_width),round(g['y']*prs.slide_height),round(g['w']*prs.slide_width),round(g['h']*prs.slide_height))


def set_geometry(shape,g,prs):shape.left,shape.top,shape.width,shape.height=positions(g,prs)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['init','apply-one','status','restore','check-slide','review-slide','finalize']); parser.add_argument('pptx'); parser.add_argument('--request'); parser.add_argument('--presentation-id'); parser.add_argument('--template'); parser.add_argument('--slide-id'); parser.add_argument('--revision',type=int); parser.add_argument('--base-revision',type=int); parser.add_argument('--operation-id'); parser.add_argument('--notes',default=''); parser.add_argument('--passed',action='store_true'); parser.add_argument('--reviewer',choices=['human','vision_model'],default='vision_model'); args=parser.parse_args(); store=PresentationStore(args.pptx)
    if args.command=='init':result=store.init(args.presentation_id,args.template)
    elif args.command=='apply-one':result=store.apply_one(json.loads(Path(args.request).read_text()),Path(args.pptx).parent)
    elif args.command=='status':result=store.status()
    elif args.command=='restore':result=store.restore(args.revision,args.base_revision,args.operation_id)
    elif args.command=='check-slide':result=store.check_slide(args.slide_id,args.notes)
    elif args.command=='review-slide':result=store.review(args.slide_id,args.revision,args.passed,args.notes,args.reviewer)
    else:result=store.finalize(args.base_revision)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception as exc:print(json.dumps(dict(status='rejected',error=str(exc))),file=sys.stderr);sys.exit(1)
