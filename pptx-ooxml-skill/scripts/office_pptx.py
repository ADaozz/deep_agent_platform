#!/usr/bin/env python3
"""Element-atomic PPTX engine, using the platform's shared artifact transaction store.

Design behaviour lives in ``design/``: profiles provide tokens, layouts provide
semantic slots, components provide reviewed SVG instances. This module only
executes validated atomic operations; it never plans pages or draws new SVG.
"""
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import time

from pptx import Presentation
from pptx.util import Inches,Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE,MSO_CONNECTOR,MSO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE,XL_LEGEND_POSITION
from pptx.oxml.xmlchemy import OxmlElement
from pptx.oxml.ns import qn
from pptx.opc.package import Part
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from lxml import etree

SKILL_ROOT=Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:sys.path.insert(0,str(SKILL_ROOT))
from design import design_quality, diagram_presets, layout_registry, layout_resolver, profile_registry


def load(name):
    path=Path(__file__).with_name(name+'.py')
    if name=='artifact_runtime' and not path.exists():path=Path(__file__).resolve().parents[2]/'platform_app'/'artifact_runtime.py'
    spec=importlib.util.spec_from_file_location(name,path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

shared=load('artifact_runtime'); renderer=load('slide_renderer'); validator=load('slide_validator'); component_renderer=load('component_renderer')
native_style=load('native_style'); native_diagram=load('native_diagram')
template_importer=load('template_importer'); template_clone=load('template_clone')
template_recolor=load('template_recolor'); template_fill=load('template_fill')

DEFAULT_PROFILE='corporate_blue'
SVG_BLIP_URI='{96DAC541-7B7A-43D3-8B79-37D633B846F1}'
SVG_NAMESPACE='http://schemas.microsoft.com/office/drawing/2016/SVG/main'
# Legacy constants stay importable for older callers; new code reads profiles.
THEMES={
 'corporate_blue':dict(background='FFFFFF',text='17324D',accent='246BCE',muted='536B82',card='EDF4FC'),
 'slate_dark':dict(background='14263D',text='FFFFFF',accent='66B5FF',muted='BCD0E4',card='203B58')}
STYLES={'slide_title':dict(font_size=30,bold=True,color_token='text'),'cover_title':dict(font_size=40,bold=True,color_token='text'),
 'body':dict(font_size=20,bold=False,color_token='text'),'caption':dict(font_size=12,bold=False,color_token='muted'),'card_title':dict(font_size=24,bold=True,color_token='accent')}

_PROFILES=_LAYOUTS=_COMPONENTS=None


def profiles():
    global _PROFILES
    if _PROFILES is None:_PROFILES=profile_registry.load_registry()
    return _PROFILES


def layouts():
    global _LAYOUTS
    if _LAYOUTS is None:_LAYOUTS=layout_registry.load_layouts()
    return _LAYOUTS


def registry():
    global _COMPONENTS
    if _COMPONENTS is None:_COMPONENTS=component_renderer.load()
    return _COMPONENTS


def identifier(value):return shared.identifier(value)


def component_validator_get(component_id):return component_renderer.components.get(registry(),component_id)


def palette_of(profile):
    return {key:value for key,value in profile['colors'].items() if key!='chart_series'}


def _blend(first,second,ratio):
    def channel(value,index):return int(value[index:index+2],16)
    mixed=[round(channel(first,index)*(1-ratio)+channel(second,index)*ratio) for index in (0,2,4)]
    return ''.join(f'{max(0,min(255,item)):02X}' for item in mixed)


def semantic_palette(tokens):
    """Map a theme clrScheme onto the names new objects already use."""
    tokens=tokens or {}
    accent=tokens.get('accent1'); background=tokens.get('lt1') or tokens.get('lt2'); text=tokens.get('dk1'); muted=tokens.get('dk2') or tokens.get('accent2')
    if not accent or not background or not text:return {}
    muted=muted or text
    return dict(accent=accent.upper(),background=background.upper(),text=text.upper(),muted=muted.upper(),
        card=_blend(background,muted,0.12),border=muted.upper())


def apply_effective_palette(state,palette):
    """Document colors win over the profile palette. The original template tokens stay untouched."""
    theme=state.setdefault('theme',{})
    for key in ('accent','background','text','muted','card','border'):
        if palette.get(key):theme[key]=palette[key].upper()
    if not palette.get('card') and palette.get('background') and palette.get('muted'):
        theme['card']=_blend(palette['background'],palette['muted'],0.12)
    theme['surface']=theme.get('card',theme.get('background','FFFFFF'))
    if not palette.get('border') and palette.get('muted'):theme['border']=palette['muted'].upper()
    theme['accent_soft']=_blend(theme.get('background','FFFFFF'),theme.get('accent','246BCE'),0.25)
    state['effective_palette']={key:theme[key] for key in ('accent','background','text','muted','card','border') if key in theme}


def apply_profile(state,profile_id):
    profile=profiles().get(profile_id)
    kept=dict(state.get('effective_palette') or {})
    state.update(profile_id=profile_id,profile=profile,theme_name=profile_id,theme=palette_of(profile))
    conflicts=[]
    accent=state.get('template_accent')
    # A palette already chosen for this document stays in force. set_profile changes
    # type scale and layout policy, not colors the user or the template already set.
    if kept:
        apply_effective_palette(state,kept)
    elif accent:
        background=state['theme']['background'];card=state['theme']['card']
        ratios=[design_quality.contrast(accent,background),design_quality.contrast(accent,card)]
        if min(ratios)>=3.0:state['theme']['accent']=accent
        else:conflicts.append(dict(kind='template_accent_contrast',accent=accent,profile=profile_id,
            contrast=min(ratios),kept_accent=state['theme']['accent'],
            message='Inherited template accent '+accent+' is unreadable on profile '+profile_id+'; the profile accent is used instead'))
    state['design_conflicts']=conflicts
    state['font_family']=state.get('template_font') or profile['fonts']['body']
    return profile


def profile_of(state):
    if 'profile' not in state:
        apply_profile(state,state.get('profile_id') or state.get('theme_name') or DEFAULT_PROFILE)
    return state['profile']


def style_for(state,token):
    profile=profile_of(state)
    if token not in profile['type_scale']:
        raise ValueError(f'Unknown style token {token!r}; available: '+', '.join(profile['type_scale']))
    item=profile['type_scale'][token]
    return dict(style_token=token,font_family=state.get('template_font') or profile['fonts'][item['font']],
        font_size=item['size'],bold=item['bold'],color=state['theme'].get(item['color_token'],profile['colors'][item['color_token']]),
        line_height=item['line_height'],letter_spacing=item.get('letter_spacing'))


def slot_views(state,layout_id,modules):
    key,defs=layouts().variant_for(layout_id,modules)
    if key is None:raise ValueError(f'Layout {layout_id} has no variant for {modules} modules; available: '+', '.join(map(str,layouts().get(layout_id)['module_counts'])))
    views={name:layout_resolver.slot_view(slot,profile_of(state),lambda token:style_for(state,token)) for name,slot in defs.items()}
    return key,views


def set_slide_layout(state,sid,layout_id,modules):
    if not profiles().allows_layout(state['profile_id'],layout_id):raise ValueError(f'Profile {state["profile_id"]} does not enable layout {layout_id}')
    key,views=slot_views(state,layout_id,modules)
    state.setdefault('layouts',{})[sid]=layout_id
    state.setdefault('layout_variants',{})[sid]=key
    state.setdefault('layout_modules',{})[sid]=modules
    state.setdefault('layout_slots',{})[sid]={name:view['rect'] for name,view in views.items()}
    return views


def check_anchor_content(state,sid,anchor,content):
    """Reject content that the declared slot cannot hold, before mutating."""
    layout_id=state.get('layouts',{}).get(sid)
    if not layout_id or not anchor:return
    if anchor in state.get('slots',{}).get(sid,{}):return
    modules=state.get('layout_modules',{}).get(sid,0)
    try:key,definitions=layouts().variant_for(layout_id,modules)
    except ValueError:return
    if not definitions or anchor not in definitions:return
    problem=layouts().capacity_error(layout_id,modules,anchor,content)
    if problem:raise ValueError(problem)


def normalize_state(state):
    """Upgrade a pre-design-system state without rewriting its package."""
    if 'profile' not in state:
        apply_profile(state,state.get('profile_id') or state.get('theme_name') or DEFAULT_PROFILE)
        # Reviews produced before the design system never ran the new checks.
        state['reviews']={};state['renders']={};state['repair_attempts']={}
    for key in ('slots','layouts','layout_variants','layout_modules','layout_slots'):
        state.setdefault(key,{})
    for sid,layout_id in list(state['layouts'].items()):
        if state['layout_slots'].get(sid):continue
        try:
            modules=state['layout_modules'].get(sid,layouts().get(layout_id)['default_modules'])
            key,views=slot_views(state,layout_id,modules)
        except ValueError:continue
        state['layout_variants'][sid]=key;state['layout_modules'][sid]=modules
        state['layout_slots'][sid]={name:view['rect'] for name,view in views.items()}


def anchors_for(state,sid):
    merged=dict(state.get('layout_slots',{}).get(sid,{}))
    merged.update(state.get('slots',{}).get(sid,{}))
    return merged


def resolve_style(state,element,views=None):
    token=element.get('style') or element.get('style_token')
    if not token and views:
        anchor=element.get('anchor');slot=views.get(anchor) if anchor else None
        token=slot['style_token'] if slot else None
    return style_for(state,token or 'body')


def geometry(element,anchors):
    if element.get('anchor'):
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
    align={'left':PP_ALIGN.LEFT,'center':PP_ALIGN.CENTER,'right':PP_ALIGN.RIGHT}.get(style.get('align'))
    for i,line in enumerate(text.split('\n')):
        p=frame.paragraphs[0] if i==0 else frame.add_paragraph(); p.text=line; p.space_after=Pt(4)
        p.line_spacing=max(.9,min(2.0,round(float(style.get('line_height',1.2))/1.2,3)))
        if align is not None:p.alignment=align
        for run in p.runs:
            run.font.name=style['font_family']; run.font.size=Pt(style['font_size']); run.font.bold=style['bold']; run.font.color.rgb=RGBColor.from_string(style['color'])
            if style.get('italic') is not None:run.font.italic=style['italic']
            rpr=run._r.get_or_add_rPr(); east=OxmlElement('a:ea'); east.set('typeface',style['font_family']); rpr.append(east)
            if style.get('letter_spacing'):rpr.set('spc',str(int(round(float(style['letter_spacing'])*100))))


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


def positions(g,prs):return (round(g['x']*prs.slide_width),round(g['y']*prs.slide_height),round(g['w']*prs.slide_width),round(g['h']*prs.slide_height))


def set_geometry(shape,g,prs):shape.left,shape.top,shape.width,shape.height=positions(g,prs)


def check_color(color):
    if not isinstance(color,str) or len(color)!=6 or any(c not in '0123456789abcdefABCDEF' for c in color):raise ValueError('Invalid RGB color')


def serialize(prs):
    out=io.BytesIO(); prs.save(out); data=out.getvalue(); validator.validate_package(data); return data


def _require_checked(state,prs):
    """Content slides must pass check-slide before another one is added. Prototypes are layout sources, not delivery pages."""
    prototypes=set(state.get('prototype_slides') or [])
    for slide in prs.slides:
        sid=renderer.slide_identity(slide)
        if sid in prototypes:continue
        review=state['reviews'].get(sid,{})
        if review.get('status')=='blocked' or not review.get('automatic_passed') or (state['mode']=='strict' and review.get('visual_review')!='passed'):
            raise ValueError('Check all affected slides before creating the next slide')


def find_slide(prs,sid):
    values=[s for s in prs.slides if renderer.slide_identity(s)==sid]
    if len(values)!=1:raise ValueError('Unknown or ambiguous stable slide ID')
    return values[0]


def find_element(slide,eid):
    values=[s for s in slide.shapes if renderer.element_identity(s)==eid]
    if len(values)!=1:raise ValueError('Unknown or ambiguous element ID')
    return values[0]


def component_children(slide,instance_id):
    return [shape for shape in slide.shapes if renderer.metadata(shape).get('component_instance')==instance_id]


def svg_rel_id(shape):
    """Relationship id of the vector source attached to a picture, if any."""
    blip=shape._element.blipFill.blip
    ext_lst=blip.find(qn('a:extLst'))
    if ext_lst is None:return None
    node=ext_lst.find('.//{%s}svgBlip'%SVG_NAMESPACE)
    return node.get(qn('r:embed')) if node is not None else None


def attach_svg_source(shape,svg_bytes):
    """Embed the reviewed SVG as the picture's vector source with PNG fallback.

    PowerPoint 2016+ uses the SVG; LibreOffice and older viewers use the
    fallback PNG. No SVG-to-shape conversion is performed.
    """
    package=shape.part.package
    part=Part(package.next_partname('/ppt/media/image%d.svg'),'image/svg+xml',package,bytes(svg_bytes))
    relationship=shape.part.relate_to(part,RT.IMAGE)
    blip=shape._element.blipFill.blip
    ext_lst=blip.find(qn('a:extLst'))
    if ext_lst is None:ext_lst=etree.SubElement(blip,qn('a:extLst'))
    extension=etree.SubElement(ext_lst,qn('a:ext'));extension.set('uri',SVG_BLIP_URI)
    svg_blip=etree.SubElement(extension,'{%s}svgBlip'%SVG_NAMESPACE,nsmap={'asvg':SVG_NAMESPACE})
    svg_blip.set(qn('r:embed'),relationship)
    return relationship


def add_svg_picture(slide,png_path,svg_bytes,left,top,width,height):
    shape=slide.shapes.add_picture(str(png_path),left,top,width,height)
    attach_svg_source(shape,svg_bytes)
    return shape


def component_size_in(geometry_value,prs):
    return (geometry_value['w']*prs.slide_width/914400,geometry_value['h']*prs.slide_height/914400)


def check_component_geometry(entry,geometry_value,prs,aspect_key='aspect_ratio'):
    width_in,height_in=component_size_in(geometry_value,prs)
    aspect=width_in/height_in if height_in else 0
    if abs(aspect-entry[aspect_key])>entry[aspect_key]*.02:
        raise ValueError(f'Component geometry must keep the {entry[aspect_key]:.3f}:1 canvas ratio; received {aspect:.3f}:1')
    minimum=entry['min_size_in']
    if width_in+1e-6<minimum['w'] or height_in+1e-6<minimum['h']:
        raise ValueError(f'Component {entry["component_id"]} needs at least {minimum["w"]:.2f} x {minimum["h"]:.2f} in; received {width_in:.2f} x {height_in:.2f} in')


def add_component(slide,element,state,sid,prs,anchors,views):
    entry=component_validator_get(element['component'])
    g=geometry(element,anchors)
    check_component_geometry(entry,g,prs)
    result=component_renderer.render(registry(),profiles(),state['profile_id'],element['component'],
        element.get('params') or {},element.get('texts') or {},element.get('version'))
    if element.get('layer','content') not in ('content','background','decoration'):raise ValueError('Unknown layer intent')
    temporary=Path(tempfile.mkstemp(suffix='.png')[1])
    try:
        temporary.write_bytes(result['png'])
        shape=add_svg_picture(slide,temporary,result['svg'],*positions(g,prs))
    finally:
        temporary.unlink(missing_ok=True)
    shape.name='da:'+element['id']
    group=element.get('overlap_group') or 'component:'+element['id']
    meta=dict(kind='component',layer=element.get('layer','content'),anchor=element.get('anchor'),overlap_group=group,vector=True,
        component=dict(component_id=result['component_id'],version=result['version'],variant=result['variant'],
        params=result['params'],texts=result['texts'],svg_sha256=result['svg_sha256'],
        svg_template_sha256=result['svg_template_sha256'],profile_id=state['profile_id'],vector=True))
    renderer.set_metadata(shape,meta)
    children=[]
    for child in component_renderer.text_children(element['id'],entry,entry['variants'][result['variant']],result['texts'],g):
        if any(renderer.element_identity(item)==child['id'] for item in slide.shapes):raise ValueError('Duplicate stable element ID')
        textbox=slide.shapes.add_textbox(*positions(child['geometry'],prs))
        child_style=style_for(state,child['style']);child_style['align']=child['align']
        apply_text(textbox,child['text'],child_style)
        textbox.name='da:'+child['id']
        renderer.set_metadata(textbox,dict(kind='text',style=child['style'],text=child['text'],resolved_style=child_style,
            align=child['align'],layer='content',overlap_group=group,component_instance=element['id'],component_slot=child['slot']))
        children.append(child)
    meta['texts']={child['slot']:child['text'] for child in children}
    renderer.set_metadata(shape,meta)
    return shape,children


def rebuild_component(slide,shape,state,prs):
    """Re-render one component instance with its recorded version and parameters."""
    meta=renderer.metadata(shape); record=meta.get('component') or {}
    entry=component_validator_get(record.get('component_id'))
    result=component_renderer.render(registry(),profiles(),state['profile_id'],record['component_id'],
        record.get('params') or {},record.get('texts') or {},record.get('version'))
    left,top,width,height=shape.left,shape.top,shape.width,shape.height
    parent=shape._element.getparent();index=parent.index(shape._element);name=shape.name
    previous_svg=svg_rel_id(shape)
    g=dict(zip(('x','y','w','h'),(left/prs.slide_width,top/prs.slide_height,width/prs.slide_width,height/prs.slide_height)))
    check_component_geometry(entry,g,prs)
    temporary=Path(tempfile.mkstemp(suffix='.png')[1])
    try:
        temporary.write_bytes(result['png'])
        replacement=add_svg_picture(slide,temporary,result['svg'],left,top,width,height)
    finally:
        temporary.unlink(missing_ok=True)
    replacement.name=name
    parent.remove(replacement._element);parent.insert(index,replacement._element)
    meta['overlap_group']=meta.get('overlap_group') or 'component:'+record['component_id']
    meta['vector']=True
    meta['component']=dict(record,component_id=result['component_id'],version=result['version'],variant=result['variant'],
        params=result['params'],texts=result['texts'],svg_sha256=result['svg_sha256'],
        svg_template_sha256=result['svg_template_sha256'],profile_id=state['profile_id'],vector=True)
    renderer.set_metadata(replacement,meta)
    parent.remove(shape._element)
    if previous_svg:
        try:slide.part.drop_rel(previous_svg)
        except KeyError:pass
    return replacement,g


def sync_component_children(slide,instance_id,g,state,prs,record):
    entry=component_validator_get(record['component_id'])
    variant=entry['variants'][record['variant']]
    wanted=component_renderer.text_children(instance_id,entry,variant,record.get('texts') or {},g)
    existing={renderer.element_identity(shape):shape for shape in component_children(slide,instance_id)}
    keep=set()
    for child in wanted:
        keep.add(child['id']);style=style_for(state,child['style']);style['align']=child['align']
        shape=existing.get(child['id'])
        if shape is None:
            shape=slide.shapes.add_textbox(*positions(child['geometry'],prs));shape.name='da:'+child['id']
        set_geometry(shape,child['geometry'],prs)
        apply_text(shape,child['text'],style)
        renderer.set_metadata(shape,dict(kind='text',style=child['style'],text=child['text'],resolved_style=style,
            align=child['align'],layer='content',overlap_group='component:'+instance_id,component_instance=instance_id,component_slot=child['slot']))
    for element_id,shape in existing.items():
        if element_id not in keep:shape._element.getparent().remove(shape._element)


def apply_table_style(shape,state,meta=None):
    meta=meta if meta is not None else renderer.metadata(shape)
    native_style.apply_table(shape,meta,state,profile_of(state)['tables'])
    for row in shape.table.rows:
        for cell in row.cells:
            for paragraph in cell.text_frame.paragraphs:
                for run in paragraph.runs:run.font.name=state['font_family']


def apply_chart_style(shape,state,unit=None,meta=None):
    if meta is None:
        meta=renderer.metadata(shape)
        if 'chart' not in meta:meta=dict(chart=dict(type='bar',categories=[],series=[dict(name='',values=[])]))
    if unit is not None:meta.setdefault('chart',{})['unit']=unit
    native_style.apply_chart(shape,meta,state,profile_of(state))


def apply_element_style(shape,meta,state,views=None):
    """Re-apply profile defaults, then preset and explicit overrides, for one element."""
    kind=meta.get('kind')
    if kind=='text':
        meta['resolved_style']=resolve_style(state,meta,views)
        if meta.get('align'):meta['resolved_style']['align']=meta['align']
        meta['resolved_style']=native_style.text_style(meta,state)
        apply_text(shape,meta.get('text') or shape.text,meta['resolved_style'])
    elif kind=='shape':
        if meta.get('fill_token'):
            meta['fill']=state['theme'].get(meta['fill_token'],meta.get('fill'))
            if meta.get('shape')=='line':shape.line.color.rgb=RGBColor.from_string(meta['fill'])
            else:shape.fill.solid();shape.fill.fore_color.rgb=RGBColor.from_string(meta['fill'])
        native_style.apply_shape(shape,meta,state)
    elif kind=='table':apply_table_style(shape,state,meta)
    elif kind=='chart':apply_chart_style(shape,state,meta=meta)
    renderer.set_metadata(shape,meta)


def current_geometry(shape,prs):
    return (shape.left/prs.slide_width,shape.top/prs.slide_height,shape.width/prs.slide_width,shape.height/prs.slide_height)


def infer_kind(shape):
    if shape.has_chart:return 'chart'
    if shape.has_table:return 'table'
    if shape.shape_type==MSO_SHAPE_TYPE.PICTURE:return 'image'
    return 'text' if shape.has_text_frame else 'shape'


def adjust_element(slide,element,state,prs,anchors):
    """Relative move/scale/rotate from the element's current geometry; the rest of the page is untouched."""
    if not isinstance(element,dict) or 'id' not in element:raise ValueError('element.adjust requires element.id')
    shape=find_element(slide,element['id']);meta=renderer.metadata(shape)
    imported=not meta.get('kind')
    if imported:
        kind=infer_kind(shape)
    else:
        kind=meta['kind']
    if not imported and meta.get('component_instance'):raise ValueError('Component text moves with its component; adjust the component instead')
    if not imported and meta.get('diagram_role')=='edge':raise ValueError('Connectors follow their nodes; adjust a node or use diagram.update')
    request={key:value for key,value in element.items() if key!='id'}
    delta=request.pop('rotate',None)
    keep_aspect=kind in ('component','image') or bool(meta.get('icon'))
    if request:
        g=native_style.adjusted_geometry(current_geometry(shape,prs),request,prs.slide_width,prs.slide_height,keep_aspect)
        if kind=='component':check_component_geometry(component_validator_get(meta['component']['component_id']),g,prs)
        set_geometry(shape,g,prs)
        if kind=='component':sync_component_children(slide,element['id'],g,state,prs,meta['component'])
    if delta is not None:
        if kind in ('chart','table','component') or meta.get('shape')=='line' or meta.get('diagram_id'):
            raise ValueError(f'{kind} elements are not rotated; rotation is limited to text, images and simple shapes')
        shape.rotation=native_style.rotation(shape.rotation,delta)
        if not imported:meta['rotation']=shape.rotation
    if imported:return
    if meta.get('anchor'):meta['manual_geometry']=True
    renderer.set_metadata(shape,meta)
    if meta.get('diagram_id'):native_diagram.reroute(slide,meta['diagram_id'],state,prs)


def restyle_presentation(prs,state):
    """Re-apply the active profile to every generated object and slide background."""
    for slide in prs.slides:
        sid=renderer.slide_identity(slide)
        layout_id=state.get('layouts',{}).get(sid)
        views=None
        if layout_id:
            modules=state.get('layout_modules',{}).get(sid,layouts().get(layout_id)['default_modules'])
            _,views=slot_views(state,layout_id,modules)
            state['layout_slots'][sid]={name:view['rect'] for name,view in views.items()}
        anchors=anchors_for(state,sid)
        background=state['theme']['background']
        slide.background.fill.solid();slide.background.fill.fore_color.rgb=RGBColor.from_string(background)
        state.setdefault('backgrounds',{})[sid]=background
        components_to_rebuild=[]
        diagrams=set()
        for shape in list(slide.shapes):
            meta=renderer.metadata(shape)
            if not meta.get('kind'):continue
            if meta.get('diagram_id'):
                diagrams.add(meta['diagram_id']);continue
            if meta['kind'] in ('text','shape','table','chart'):apply_element_style(shape,meta,state,views)
            elif meta['kind']=='component':components_to_rebuild.append(shape)
            if meta.get('anchor') and meta['anchor'] in anchors and not meta.get('manual_geometry'):set_geometry(shape,geometry(meta,anchors),prs)
        for diagram_id in sorted(diagrams):native_diagram.rebuild(slide,diagram_id,state,prs,anchors)
        for shape in components_to_rebuild:
            meta=renderer.metadata(shape);replacement,g=rebuild_component(slide,shape,state,prs)
            sync_component_children(slide,renderer.element_identity(replacement),g,state,prs,renderer.metadata(replacement)['component'])


native_diagram.configure(renderer=renderer,native_style=native_style,style_for=style_for,apply_text=apply_text,
    geometry=geometry,identifier=identifier,anchors=anchors_for,slide_id=renderer.slide_identity)


class PresentationStore(shared.ArtifactStore):
    def __init__(self,path,temporary_root=None):super().__init__(path,'.pptx','.pptx-state',temporary_root)
    def assert_current(self,state,base_revision=None):
        if state is None:raise ValueError('Presentation not initialized')
        if base_revision is not None and (type(base_revision) is not int or base_revision!=state['revision']):raise ValueError('Revision conflict')
        if not self.path.exists() or hashlib.sha256(self.path.read_bytes()).hexdigest()!=state['sha256']:raise ValueError('External modification; inspect or restore the last snapshot')
    def design_event(self,state):
        return dict(profile_id=state.get('profile_id'),theme=state.get('theme'),font_family=state.get('font_family'),
            layouts=state.get('layouts',{}),layout_variants=state.get('layout_variants',{}),layout_modules=state.get('layout_modules',{}),
            layout_slots=state.get('layout_slots',{}),backgrounds=state.get('backgrounds',{}),
            template_font=state.get('template_font'),template_accent=state.get('template_accent'),
            conflicts=state.get('design_conflicts',[]),
            template_manifest=state.get('template_manifest'),template_slots=state.get('template_slots',{}),
            effective_palette=state.get('effective_palette'),template_tokens=state.get('template_tokens'),
            protected_elements=state.get('protected_elements'),template_use=state.get('template_use'),
            prototype_slides=state.get('prototype_slides'))
    def commit(self,c,state,data,operation_id,request,changed,kind='presentation_revision',extra=None):
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
            manifest=view,path=self.path.name,theme=state['theme'],font_family=state['font_family'],profile_id=state.get('profile_id'),
            design=self.design_event(state),committed_at=time.time())
        if extra:event.update(extra)
        self.persist_revision(c,state,data,event,request,operation_id,kind)
        return event
    def init(self,presentation_id,template=None,mode='normal',theme=DEFAULT_PROFILE,profile=None,use='edit_existing'):
        identifier(presentation_id)
        profile_id=profile or theme
        if mode not in ('normal','strict'):raise ValueError('Unknown review mode')
        if use not in ('edit_existing','template_reference'):raise ValueError('use must be edit_existing or template_reference')
        profiles().get(profile_id)
        with self.locked() as (c,state):
            if state:
                if state['presentation_id']!=presentation_id:raise ValueError('Presentation identity mismatch')
                return self.view(c,state)
            if self.path.exists():raise ValueError('File already exists')
            if template:
                validator.validate_package(Path(template).read_bytes()); prs=Presentation(template)
            else:
                prs=Presentation(); prs.slide_width=Inches(13.333333); prs.slide_height=Inches(7.5)
            state=dict(presentation_id=presentation_id,revision=-1,mode=mode,slots={},layouts={},layout_variants={},layout_modules={},
                layout_slots={},backgrounds={},reviews={},renders={},repair_attempts={},template=bool(template),template_complex={},
                template_use=use if template else 'blank',prototype_slides=[],protected_elements={})
            apply_profile(state,profile_id)
            if template:
                from pptx.opc.constants import RELATIONSHIP_TYPE as RT
                from lxml import etree
                theme_part=prs.slide_masters[0].part.part_related_by(RT.THEME)
                xml=etree.fromstring(theme_part.blob)
                ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
                accent=xml.xpath('string(//a:clrScheme/a:accent1/a:srgbClr/@val)',namespaces=ns)
                if len(accent)==6:state['template_accent']=accent.upper()
                faces=xml.xpath('//a:fontScheme/a:minorFont/a:ea/@typeface',namespaces=ns)
                if faces and faces[0]:state['template_font']=faces[0]
                apply_profile(state,profile_id)
                manifest=template_importer.index_loaded(prs,template)
                state['template_manifest']=manifest
                state['template_slots']=manifest.get('slots_by_slide') or {}
                state['template_tokens']=dict(manifest.get('tokens') or {})
                state['protected_elements']={slide['slide_id']:list(slide.get('preserve') or []) for slide in manifest.get('slides') or []}
                # An unreadable template accent stays recorded in template_tokens. The profile
                # accent remains the effective color and the existing contrast conflict is kept.
                if not state.get('design_conflicts'):
                    derived=semantic_palette(state['template_tokens'])
                    if derived:
                        state['effective_palette']=derived
                        apply_effective_palette(state,derived)
                if use=='template_reference':
                    state['prototype_slides']=[renderer.slide_identity(slide) for slide in prs.slides]
            for slide in prs.slides:
                sid=renderer.slide_identity(slide)
                state['template_complex'][sid]=bool(template)
            state['active_slide']=renderer.slide_identity(prs.slides[0]) if len(prs.slides) else None
            return self.commit(c,state,serialize(prs),'__init__',{'template':str(template)},['*'],'presentation_initialized')
    def view(self,c,state):
        if state is None:raise ValueError('Presentation not initialized')
        data=c.execute('SELECT docx FROM revisions WHERE revision=?',(state['revision'],)).fetchone()[0]
        return dict(presentation_id=state['presentation_id'],revision=state['revision'],sha256=state['sha256'],path=self.path.name,
            manifest=renderer.manifest(Presentation(io.BytesIO(data)),state),active_slide=state.get('active_slide'),finalized=state.get('finalized',False),
            mode=state['mode'],theme=state['theme'],profile_id=state.get('profile_id'),deck_review=state.get('deck_review'),
            template_index=_template_index(state),
            history=[{key:value for key,value in json.loads(r[0]).items() if key in ('revision','operation_id','sha256','slide_id','status')} for r in c.execute('SELECT result FROM revisions ORDER BY revision')])
    def status(self):
        with self.locked() as (c,state):return self.view(c,state)
    def track_external(self, presentation_id):
        """Index a valid existing PPTX without changing its package bytes."""
        with self.locked() as (c, state):
            if state is not None:
                return None
            data = self.path.read_bytes()
            validator.validate_package(data)
            prs = Presentation(io.BytesIO(data))
            state = dict(presentation_id=identifier(presentation_id), revision=-1, mode='normal',
                slots={}, layouts={}, layout_variants={}, layout_modules={}, layout_slots={}, backgrounds={},
                reviews={}, renders={}, repair_attempts={}, template=True,
                template_complex={renderer.slide_identity(slide): True for slide in prs.slides},
                active_slide=renderer.slide_identity(prs.slides[0]) if len(prs.slides) else None)
            apply_profile(state,DEFAULT_PROFILE)
            return self.commit(c, state, data, '__external__', {'external': True}, ['*'], 'presentation_initialized')
    def preview(self):
        """Generate display images while retaining pending content/visual checks."""
        current = self.status()
        revision = current['revision']
        reviewer = load('slide_reviewer')
        reviewer.render_snapshot(self, revision)
        with self.locked() as (c, state):
            self.assert_current(state, revision)
            for index, slide in enumerate(current['manifest']['slides']):
                state['renders'][slide['slide_id']] = dict(revision=revision, image=f'slide-{index+1:03d}.png')
            return self.emit_state(c, state, dict(type='presentation_preview',
                presentation_id=state['presentation_id'], revision=revision, path=self.path.name,
                manifest=self.view(c, state)['manifest']))
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
            state=copy.deepcopy(state); normalize_state(state); op=request['operation']; sid=request.get('slide_id'); changed=[sid] if sid else ['*']; extra=None
            if op=='slide.add':
                _require_checked(state,prs)
                identifier(sid)
                if any(renderer.slide_identity(s)==sid for s in prs.slides):raise ValueError('Duplicate slide ID')
                index=request.get('layout_index',next((i for i,layout in enumerate(prs.slide_layouts) if layout.name.lower()=='blank'),0))
                if type(index) is not int or not 0<=index<len(prs.slide_layouts):raise ValueError('Unknown template layout')
                layout_id=request.get('layout','title_body')
                modules=request.get('modules',layouts().get(layout_id)['default_modules'])
                if type(modules) is not int:raise ValueError('modules must be an integer')
                views=set_slide_layout(state,sid,layout_id,modules)
                slide=prs.slides.add_slide(prs.slide_layouts[index]); slide._element.cSld.set('name','da-slide:'+sid)
                state.setdefault('slots',{})[sid]={}
                if state['template']:
                    for placeholder in slide.placeholders:
                        name='title' if placeholder.is_placeholder and placeholder.placeholder_format.type in (1,3) else 'body'
                        state['slots'][sid][name]=(placeholder.left/prs.slide_width,placeholder.top/prs.slide_height,placeholder.width/prs.slide_width,placeholder.height/prs.slide_height)
                state['active_slide']=sid
                if state['template']:state['template_complex'][sid]=True
                slide.background.fill.solid(); slide.background.fill.fore_color.rgb=RGBColor.from_string(state['theme']['background']); state['backgrounds'][sid]=state['theme']['background']
                state['layout_slots'][sid]={name:view['rect'] for name,view in views.items()}
            elif op in ('presentation.set_theme','presentation.set_profile'):
                target=request.get('profile') or request.get('theme')
                apply_profile(state,target); changed=['*']
                restyle_presentation(prs,state)
            elif op=='slide.reorder':
                slide=find_slide(prs,sid); index=request['index']
                if type(index)is not int or not 0<=index<len(prs.slides):raise ValueError('Invalid slide order')
                node=next(n for n in prs.slides._sldIdLst if int(n.get('id'))==slide.slide_id); prs.slides._sldIdLst.remove(node); prs.slides._sldIdLst.insert(index,node); changed=['*']
            elif op=='slide.remove':
                slide=find_slide(prs,sid)
                template_clone.remove_slide(prs,slide)
                for bucket in ('reviews','template_slots','protected_elements','layouts','layout_slots','backgrounds'):
                    state.get(bucket,{}).pop(sid,None)
                prototypes=state.get('prototype_slides') or []
                state['prototype_slides']=[item for item in prototypes if item!=sid]
                changed=['*']
            elif op=='presentation.discard_prototypes':
                if state.get('template_use')!='template_reference':raise ValueError('discard_prototypes applies to a template_reference deck')
                for prototype in list(state.get('prototype_slides') or []):
                    template_clone.remove_slide(prs,find_slide(prs,prototype))
                    for bucket in ('reviews','template_slots','protected_elements','layouts','layout_slots','backgrounds'):
                        state.get(bucket,{}).pop(prototype,None)
                removed=list(state.get('prototype_slides') or [])
                state['prototype_slides']=[]
                changed=['*']; extra=dict(discarded_prototypes=removed)
            elif op=='slide.clone':
                _require_checked(state,prs)
                identifier(sid)
                source=find_slide(prs,request['source_slide_id'])
                if any(renderer.slide_identity(s)==sid for s in prs.slides):raise ValueError('Duplicate slide ID')
                dest=template_clone.clone_slide(prs,source,sid)
                if request.get('insert_after'):
                    anchor=find_slide(prs,request['insert_after'])
                    ids=[renderer.slide_identity(s) for s in prs.slides]
                    index=ids.index(renderer.slide_identity(anchor))+1
                    node=next(n for n in prs.slides._sldIdLst if int(n.get('id'))==dest.slide_id)
                    prs.slides._sldIdLst.remove(node); prs.slides._sldIdLst.insert(index,node)
                copied=state.get('template_slots',{}).get(request['source_slide_id'])
                if copied:state.setdefault('template_slots',{})[sid]=dict(copied)
                protected=(state.get('protected_elements') or {}).get(request['source_slide_id'])
                if protected:state.setdefault('protected_elements',{})[sid]=list(protected)
                state.setdefault('template_complex',{})[sid]=state.get('template_complex',{}).get(request['source_slide_id'],True)
                if getattr(template_clone,'last_clone_report',None):extra=dict(clone=template_clone.last_clone_report)
                state['active_slide']=sid
                changed=[sid]
            elif op=='presentation.recolor':
                report=template_recolor.recolor(prs,request.get('palette'),request.get('preserve'),state.get('protected_elements') or {})
                apply_effective_palette(state,request.get('palette') or {})
                changed=['*']; extra=dict(recolor=report)
            else:
                slide=find_slide(prs,sid)
                if state.get('repair_attempts',{}).get(sid,0)>=3 and state.get('reviews',{}).get(sid,{}).get('status')=='blocked':raise ValueError('Slide repair budget exhausted; restore a known version or request review')
                views=None
                if state.get('layouts',{}).get(sid):
                    views=slot_views(state,state['layouts'][sid],state.get('layout_modules',{}).get(sid,0))[1]
                anchors=anchors_for(state,sid)
                if op=='slide.set_layout':
                    layout_id=request['layout']
                    modules=request.get('modules',state.get('layout_modules',{}).get(sid,layouts().get(layout_id)['default_modules']))
                    if not profiles().allows_layout(state['profile_id'],layout_id):raise ValueError(f'Profile {state["profile_id"]} does not enable layout {layout_id}')
                    target_views=slot_views(state,layout_id,modules)[1]
                    anchored={renderer.metadata(shape).get('anchor'):renderer.metadata(shape) for shape in slide.shapes if renderer.metadata(shape).get('anchor')}
                    missing=sorted(name for name in anchored if name not in target_views)
                    if missing:raise ValueError('Layout '+layout_id+' cannot keep anchored content: missing slot(s) '+', '.join(missing)+'; keep the layout or move those elements to explicit geometry')
                    for name,meta in anchored.items():
                        if meta.get('kind')=='text' and name not in state.get('slots',{}).get(sid,{}):
                            problem=layouts().capacity_error(layout_id,modules,name,dict(type='text',text=meta.get('text','')))
                            if problem:raise ValueError('Layout '+layout_id+' cannot hold the existing text safely: '+problem)
                    set_slide_layout(state,sid,layout_id,modules)
                    anchors=anchors_for(state,sid)
                    for shape in slide.shapes:
                        meta=renderer.metadata(shape)
                        if meta.get('anchor') and meta['anchor'] in anchors:
                            set_geometry(shape,geometry(meta,anchors),prs)
                            if meta.pop('manual_geometry',None):renderer.set_metadata(shape,meta)
                    views=target_views
                elif op=='element.remove':
                    shape=find_element(slide,request['element']['id'])
                    if renderer.metadata(shape).get('diagram_id'):raise ValueError('Diagram nodes and edges are changed through diagram.update so relations stay consistent')
                    if renderer.metadata(shape).get('kind')=='component':
                        for child in component_children(slide,request['element']['id']):child._element.getparent().remove(child._element)
                        vector=svg_rel_id(shape)
                    else:vector=None
                    shape._element.getparent().remove(shape._element)
                    if vector:
                        try:slide.part.drop_rel(vector)
                        except KeyError:pass
                elif op=='element.update':
                    element=request['element']; shape=find_element(slide,element['id']); meta=renderer.metadata(shape)
                    if set(element)-{'id','text','geometry','anchor','fill','style','layer','overlap_group','rows','chart','params','texts','component_version'}:raise ValueError('Unsupported element update field')
                    if meta.get('diagram_id'):raise ValueError('Diagram nodes and edges are changed through diagram.update so relations stay consistent')
                    target_anchor=element.get('anchor') or meta.get('anchor')
                    if 'text' in element and not meta.get('kind'):raise ValueError('Imported text keeps its formatting through slot.fill')
                    if 'chart' in element and not meta.get('kind'):raise ValueError('Imported chart data is replaced with chart.replace_data')
                    if 'rows' in element and not meta.get('kind'):raise ValueError('Imported tables are copied with block structure; edit generated tables with element.update')
                    if 'text' in element:check_anchor_content(state,sid,target_anchor,{'type':'text','text':element['text']})
                    if 'rows' in element:check_anchor_content(state,sid,target_anchor,{'type':'table'})
                    if 'chart' in element:check_anchor_content(state,sid,target_anchor,{'type':'chart'})
                    if element.get('anchor') and not ({'text','rows','chart'}&set(element)):
                        check_anchor_content(state,sid,element['anchor'],{'type':meta.get('kind','text'),'text':meta.get('text','')})
                    if meta.get('kind')=='component' and ('params' in element or 'texts' in element or 'component_version' in element):
                        record=dict(meta.get('component') or {})
                        params={**(record.get('params') or {}),**(element.get('params') or {})}
                        texts=dict(record.get('texts') or {})
                        for slot,value in (element.get('texts') or {}).items():
                            if value is None:texts.pop(slot,None)
                            else:texts[slot]=value
                        merged=dict(record,params=params,texts=texts)
                        if 'component_version' in element:merged['version']=element['component_version']
                        meta['component']=merged;renderer.set_metadata(shape,meta)
                        replacement,g=rebuild_component(slide,shape,state,prs)
                        sync_component_children(slide,element['id'],g,state,prs,renderer.metadata(replacement)['component'])
                        shape=replacement;meta=renderer.metadata(replacement)
                    if 'rows' in element:
                        rows=element['rows']
                        if not shape.has_table or len(rows)!=len(shape.table.rows) or any(len(row)!=len(shape.table.columns) or any(not isinstance(text,str) or len(text)>160 for text in row) for row in rows):
                            raise ValueError('Table updates require bounded rows with existing dimensions')
                        for r,row in enumerate(rows):
                            for col,text in enumerate(row):
                                cell=shape.table.cell(r,col);cell.text=text
                                for paragraph in cell.text_frame.paragraphs:
                                    for run in paragraph.runs:run.font.name=state['font_family'];run.font.size=Pt(profile_of(state)['tables']['font_size'])
                        meta['rows']=rows;apply_table_style(shape,state,meta)
                    if 'chart' in element:
                        if not shape.has_chart:raise ValueError('Chart data update requires a native chart')
                        if element['chart'].get('type','bar')!=meta.get('chart',{}).get('type','bar'):raise ValueError('Changing chart type uses chart.change_type, which replaces the chart object and keeps its data')
                        shape.chart.replace_data(chart_data(element['chart']));meta['chart']=element['chart'];apply_chart_style(shape,state,meta=meta)
                    if element.get('geometry') or element.get('anchor'):
                        if meta.get('kind')=='component':check_component_geometry(component_validator_get(meta['component']['component_id']),geometry(element,anchors),prs)
                        set_geometry(shape,geometry(element,anchors),prs)
                        if element.get('anchor'):meta.pop('manual_geometry',None)
                        elif meta.get('anchor'):meta['manual_geometry']=True
                        if meta.get('kind')=='component':
                            g=geometry(element,anchors);sync_component_children(slide,element['id'],g,state,prs,meta['component'])
                    if 'text'in element:
                        if not shape.has_text_frame:raise ValueError('Text update requires a text element')
                        if not meta:meta=dict(kind='text',style=element.get('style','body'))
                        meta.update(text=element['text'],style=element.get('style',meta.get('style','body')))
                        meta['resolved_style']=native_style.text_style(dict(meta,resolved_style=resolve_style(state,meta,views)),state);apply_text(shape,meta['text'],meta['resolved_style'])
                        if meta.get('component_instance'):
                            record=renderer.metadata(find_element(slide,meta['component_instance'])).get('component',{})
                            entry=registry()['components'].get(record.get('component_id'))
                            if entry and record.get('variant') in entry['variants']:
                                slot=entry['variants'][record['variant']]['text_slots'].get(meta.get('component_slot'))
                                if slot and (len(meta['text'])>slot['chars'] or meta['text'].count('\n')+1>slot['lines']):raise ValueError('Component text slot capacity exceeded')
                                record.setdefault('texts',{})[meta['component_slot']]=meta['text']
                                parent=find_element(slide,meta['component_instance']);pmeta=renderer.metadata(parent);pmeta['component']=record;renderer.set_metadata(parent,pmeta)
                    if 'fill'in element:
                        color=element['fill']; check_color(color)
                        if hasattr(shape,'fill'):shape.fill.solid();shape.fill.fore_color.rgb=RGBColor.from_string(color)
                        else:shape.line.color.rgb=RGBColor.from_string(color)
                        meta['fill']=color
                    if 'style'in element and shape.has_text_frame:
                        meta['style']=element['style']; meta['resolved_style']=native_style.text_style(dict(meta,resolved_style=resolve_style(state,meta,views)),state); apply_text(shape,shape.text,meta['resolved_style'])
                    meta.update({k:element[k] for k in ('anchor','layer','overlap_group') if k in element}); renderer.set_metadata(shape,meta)
                elif op=='element.adjust':
                    adjust_element(slide,request['element'],state,prs,anchors)
                elif op in ('element.set_style','element.apply_preset'):
                    element=request['element']; shape=find_element(slide,element['id']); meta=renderer.metadata(shape)
                    kind=native_style.style_kind(meta)
                    if not meta.get('kind') and not meta.get('diagram_id'):
                        if op!='element.set_style':raise ValueError('Imported objects do not use presets; use element.set_style or presentation.recolor')
                        template_fill.apply_imported_style(shape,element.get('style') or {})
                    elif meta.get('diagram_id'):
                        native_diagram.style_element(slide,shape,meta,element,op,state,prs,anchors)
                    else:
                        if op=='element.set_style':
                            if set(element)-{'id','style'}:raise ValueError('element.set_style accepts id and style only')
                            removals={key for key,value in (element.get('style') or {}).items() if value is None}
                            additions={key:value for key,value in (element.get('style') or {}).items() if value is not None}
                            overrides=dict(meta.get('style_overrides') or {})
                            if additions:overrides=native_style.merge_overrides(overrides,native_style.validate_style(kind,additions,state))
                            elif not removals:raise ValueError('style must change at least one field')
                            for key in removals:overrides.pop(key,None)
                            if overrides:meta['style_overrides']=overrides
                            else:meta.pop('style_overrides',None)
                        else:
                            if set(element)-{'id','preset'}:raise ValueError('element.apply_preset accepts id and preset only')
                            if element.get('preset') is None:meta.pop('preset',None)
                            else:
                                preset=native_style.preset_style(element['preset'],kind)
                                meta['preset']=element['preset']
                                overrides={key:value for key,value in (meta.get('style_overrides') or {}).items() if key not in preset}
                                if overrides:meta['style_overrides']=overrides
                                else:meta.pop('style_overrides',None)
                        if kind not in ('text','shape','connector','table','chart'):raise ValueError(f'{kind} elements do not support native style changes')
                        apply_element_style(shape,meta,state,views)
                elif op=='chart.change_type':
                    element=request['element']
                    if set(element)-{'id','type'}:raise ValueError('chart.change_type accepts id and type only')
                    shape=find_element(slide,element['id']); meta=renderer.metadata(shape)
                    if meta.get('kind')!='chart' or not shape.has_chart:raise ValueError('chart.change_type requires a native chart')
                    replacement,meta=native_style.replace_chart(slide,shape,element['type'],meta)
                    renderer.set_metadata(replacement,meta);apply_element_style(replacement,meta,state,views)
                    native_style.verify_workbook(replacement,meta['chart'])
                elif op in ('diagram.add','diagram.update'):
                    native_diagram.apply(slide,op,request.get('diagram') or {},state,prs,anchors,profile_of(state))
                elif op.startswith('element.add_'):
                    element=request['element']; identifier(element['id'])
                    if any(renderer.element_identity(s)==element['id'] for s in slide.shapes):raise ValueError('Duplicate stable element ID')
                    kind=op.removeprefix('element.add_')
                    check_anchor_content(state,sid,element.get('anchor'),{'type':kind,'text':element.get('text','')})
                    if kind=='component':
                        add_component(slide,element,state,sid,prs,anchors,views)
                        return self.commit(c,state,serialize(prs),request['operation_id'],request,changed)
                    g=geometry(element,anchors); pos=positions(g,prs)
                    meta=dict(element,kind=kind,resolved_style=resolve_style(state,element,views))
                    if meta.get('layer','content') not in ('content','background','decoration'):raise ValueError('Unknown layer intent')
                    if 'preset' in element:native_style.preset_style(element['preset'],native_style.style_kind(meta))
                    if kind=='text':
                        shape=slide.shapes.add_textbox(*pos); apply_text(shape,element['text'],meta['resolved_style'])
                    elif kind=='shape' and element.get('shape')=='line':
                        if g['w']>=g['h']:x1,y1,x2,y2=pos[0],pos[1]+pos[3]//2,pos[0]+pos[2],pos[1]+pos[3]//2
                        else:x1,y1,x2,y2=pos[0]+pos[2]//2,pos[1],pos[0]+pos[2]//2,pos[1]+pos[3]
                        shape=slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,x1,y1,x2,y2)
                        meta['shape']='line';meta['fill']=state['theme']['accent'];shape.line.color.rgb=RGBColor.from_string(meta['fill']);shape.line.width=Pt(1.5)
                    elif kind=='shape':
                        shape_type={'rectangle':MSO_AUTO_SHAPE_TYPE.RECTANGLE,'rounded_rectangle':MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,'arrow':MSO_AUTO_SHAPE_TYPE.RIGHT_ARROW,'ellipse':MSO_AUTO_SHAPE_TYPE.OVAL}.get(element.get('shape','rectangle'))
                        if shape_type is None:raise ValueError('Unsupported native shape')
                        shape=slide.shapes.add_shape(shape_type,*pos); meta['shape']=element.get('shape','rectangle')
                        meta['fill_token']=element.get('fill_token','card')
                        if meta['fill_token'] not in state['theme']:raise ValueError('Unknown fill token')
                        meta['fill']=state['theme'][meta['fill_token']]
                        shape.fill.solid(); shape.fill.fore_color.rgb=RGBColor.from_string(meta['fill']); shape.line.fill.background(); native_style.flat(shape)
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
                                    for run in paragraph.runs:run.font.name=state['font_family']; run.font.size=Pt(profile_of(state)['tables']['font_size'])
                        apply_table_style(shape,state,meta)
                    elif kind=='chart':
                        chart=element['chart']; categories=chart['categories']; series=chart['series']
                        if not 1<=len(categories)<=12 or any(not isinstance(label,str) or len(label)>80 for label in categories) or not 1<=len(series)<=4 or any(not isinstance(item.get('name'),str) or len(item['name'])>80 for item in series) or any(len(s['values'])!=len(categories) or any(type(n)not in (int,float) or not math.isfinite(n) for n in s['values']) for s in series):raise ValueError('Bounded finite editable chart data required')
                        chart_type=native_style.CHART_TYPES.get(chart.get('type','bar'))
                        if chart_type is None:raise ValueError('Unsupported native chart')
                        if chart.get('type')=='pie' and (len(series)!=1 or any(v<0 for v in series[0]['values'])):raise ValueError('A pie chart shows one non-negative series')
                        shape=slide.shapes.add_chart(chart_type,*pos,chart_data(chart)); apply_chart_style(shape,state,meta=meta)
                    else:raise ValueError('Unsupported native element type')
                    shape.name='da:'+element['id']
                    if meta.get('preset'):apply_element_style(shape,meta,state,views)
                    renderer.set_metadata(shape,meta)
                elif op=='slot.fill':
                    aliases=(state.get('template_slots') or {}).get(sid) or {}
                    for _name,shape,text in template_fill.slot_targets(slide,request.get('slots'),aliases):
                        template_fill.assert_capacity(shape,text)
                    for _name,shape,text in template_fill.slot_targets(slide,request.get('slots'),aliases):
                        template_fill.fill_shape_text(shape,text)
                elif op=='chart.replace_data':
                    shape=find_element(slide,request.get('element_id') or request.get('element',{}).get('id'))
                    extra=dict(chart=template_fill.replace_chart_data(shape,request['chart'],chart_data))
                elif op=='image.replace':
                    shape=find_element(slide,request.get('element_id') or request.get('element',{}).get('id'))
                    asset=(Path(asset_root or self.path.parent)/request['path']).resolve(); assets=Path(asset_root or self.path.parent).resolve()
                    if not asset.is_relative_to(assets) or not asset.is_file() or asset.stat().st_size>12*1024*1024:raise ValueError('Unsafe or oversized image')
                    extra=dict(image=template_fill.replace_picture(slide,shape,asset))
                else:raise ValueError('Unsupported atomic presentation operation')
            return self.commit(c,state,serialize(prs),request['operation_id'],request,changed,extra=extra)
    def prepare_restore(self,c,state,revision):
        prior=json.loads(c.execute('SELECT result FROM revisions WHERE revision=?',(revision,)).fetchone()[0])
        state=copy.deepcopy(state);design=prior.get('design') or {}
        if design:
            for key in ('theme','font_family','layouts','layout_variants','layout_modules','layout_slots','backgrounds','template_font','template_accent','design_conflicts','template_manifest','template_slots','effective_palette','template_tokens','protected_elements','template_use','prototype_slides'):
                if key in design:state[key]=design[key]
            state['profile_id']=design.get('profile_id',state.get('profile_id',DEFAULT_PROFILE));state['theme_name']=state['profile_id']
            try:state['profile']=profiles().get(state['profile_id'])
            except ValueError:apply_profile(state,DEFAULT_PROFILE)
        else:
            state['theme']=prior.get('theme',state.get('theme'));state['font_family']=prior.get('font_family',state.get('font_family'))
            state['profile_id']=state.get('profile_id') or state.get('theme_name') or DEFAULT_PROFILE;state['theme_name']=state['profile_id']
            try:state['profile']=profiles().get(state['profile_id'])
            except ValueError:apply_profile(state,DEFAULT_PROFILE)
            state['layouts']={slide['slide_id']:slide.get('layout','title_body') for slide in prior.get('manifest',{}).get('slides',[])}
            state['backgrounds']={slide['slide_id']:slide.get('background','FFFFFF') for slide in prior.get('manifest',{}).get('slides',[])}
            for sid,layout_id in state['layouts'].items():
                try:modules=layouts().get(layout_id)['default_modules'];key,views=slot_views(state,layout_id,modules)
                except ValueError:continue
                state.setdefault('layout_variants',{})[sid]=key;state.setdefault('layout_modules',{})[sid]=modules
                state.setdefault('layout_slots',{})[sid]={name:view['rect'] for name,view in views.items()}
        state['active_slide']=prior.get('slide_id')
        return state

    def snapshot_unlocked(self,c,revision):return c.execute('SELECT docx FROM revisions WHERE revision=?',(revision,)).fetchone()[0]
    def emit_state(self,c,state,event):
        self.save_state(c,state); c.execute('INSERT INTO events(data) VALUES (?)',(json.dumps(event),)); c.commit(); return event
    def check_slide(self,slide_id,content_notes,intent=None,constraints=None):
        return reviewer().check(self,slide_id,content_notes,intent,constraints)
    def current_manifest(self,c,state):
        return renderer.manifest(Presentation(io.BytesIO(self.snapshot_unlocked(c,state['revision']))),state)
    def review_event(self,c,state,kind,slide_id=None,**extra):
        event=dict(type=kind,presentation_id=state['presentation_id'],revision=state['revision'],path=self.path.name,
            manifest=self.current_manifest(c,state),deck_review=state.get('deck_review'),**extra)
        if slide_id:event.update(slide_id=slide_id,review=state['reviews'].get(slide_id))
        return self.emit_state(c,state,event)
    def record_aesthetic_review(self,slide_id,check_revision,fingerprint,result):
        """Host only: apply a model review if it still describes the current check of this slide."""
        with self.locked() as(c,state):
            if state is None:return dict(applied=False,reason='uninitialized')
            review=state.get('reviews',{}).get(slide_id)
            if not review or review.get('revision')!=check_revision or review.get('fingerprint')!=fingerprint or not review.get('automatic_passed'):
                return dict(applied=False,reason='stale')
            current=review.get('aesthetic_review') or {}
            if current.get('review_id')==result.get('review_id'):return dict(applied=True,reason='duplicate')
            if current.get('status') not in ('pending',None) and current.get('reviewed_at',0)>result.get('reviewed_at',0):
                return dict(applied=False,reason='newer_review_present')
            aesthetic=dict(result,revision=check_revision)
            # A person's decision on this same check outranks a model answer that arrives later.
            for key in ('human','dismissals'):
                if current.get(key):aesthetic[key]=current[key]
            attempts=state.setdefault('aesthetic_attempts',{}).setdefault(slide_id,{})
            if aesthetic['status']=='failed':
                escalated=[]
                # Only critical findings are repaired without asking; the rest wait for the user's decision.
                for issue in aesthetic.get('issues',[]):
                    if issue['severity']=='critical' and issue['confidence'] in ('high','medium') and not issue.get('needs_verification'):
                        key=issue.get('fingerprint') or issue['category']
                        attempts[key]=attempts.get(key,0)+1;issue['attempt']=attempts[key]
                        if attempts[key]>CRITICAL_REPAIR_ROUNDS:escalated.append(issue['id'])
                aesthetic['escalated_issues']=escalated;aesthetic['auto_repair']=not escalated
            elif aesthetic['status']=='passed':state['aesthetic_attempts'][slide_id]={}
            review['aesthetic_review']=aesthetic
            review['status']=reviewer().derive_status(review);review['visual_review']=reviewer().compat_visual(review)
            self.review_event(c,state,'presentation_aesthetic_review',slide_id)
            return dict(applied=True)
    def request_review(self,slide_id):
        """Agent: re-request a model review after a transient failure (not to fish for a pass)."""
        with self.locked() as(c,state):
            self.assert_current(state);review=state.get('reviews',{}).get(slide_id)
            if not review or not review.get('automatic_passed') or not review.get('rendered'):raise ValueError('Run check-slide successfully before requesting a vision review')
            aesthetic=review.get('aesthetic_review') or {}
            if aesthetic.get('status') not in ('unavailable',):
                raise ValueError(f'Vision review is {aesthetic.get("status","pending")}; only an unavailable review may be re-requested. Fix reported issues and run check-slide, or ask the user')
            requests=aesthetic.get('requests',0)+1
            if requests>3:raise ValueError('Vision review re-request limit reached for this check; request human visual review in the UI')
            review['aesthetic_review']=dict(status='pending',reason='re_requested',requests=requests,previous_error=aesthetic.get('error'))
            review['status']=reviewer().derive_status(review);review['visual_review']=reviewer().compat_visual(review)
            return self.review_event(c,state,'presentation_review_requested',slide_id,requested_by='agent')
    def host_request_review(self,slide_id,revision):
        """User-initiated re-review from the UI; allowed in any state of a current, passed check."""
        with self.locked() as(c,state):
            self.assert_current(state,revision);review=state.get('reviews',{}).get(slide_id)
            if not review or not review.get('automatic_passed') or not review.get('rendered'):raise ValueError('The slide needs a successful formal check first')
            previous=review.get('aesthetic_review') or {}
            review['aesthetic_review']=dict(status='pending',reason='user_requested',requests=previous.get('requests',0))
            review['status']=reviewer().derive_status(review);review['visual_review']=reviewer().compat_visual(review)
            return self.review_event(c,state,'presentation_review_requested',slide_id,requested_by='human')
    def review_status(self,slide_id=None,wait=0):
        """Agent: read (optionally wait for) aesthetic results and the next permitted action."""
        if type(wait) not in (int,float) or not 0<=wait<=180:raise ValueError('wait must be 0 to 180 seconds')
        deadline=time.time()+wait
        while True:
            with self.locked() as(c,state):
                if state is None:raise ValueError('Presentation not initialized')
                ids=[slide_id] if slide_id else [s['slide_id'] for s in self.current_manifest(c,state)['slides']]
                rows={sid:state.get('reviews',{}).get(sid) for sid in ids}
                deck=state.get('deck_review')
            pending=[sid for sid,review in rows.items() if review and (review.get('aesthetic_review') or {}).get('status')=='pending' and review.get('automatic_passed')]
            if not pending or time.time()>=deadline:break
            time.sleep(1.5)
        return dict(presentation_id=state['presentation_id'],revision=state['revision'],slides=[review_summary(sid,review) for sid,review in rows.items()],
            deck_review=deck_summary(deck) if deck else None)
    def request_deck_review(self,base_revision):
        with self.locked() as(c,state):
            self.assert_current(state,base_revision);slides=self.current_manifest(c,state)['slides']
            if len(slides)<2:raise ValueError('Deck review compares at least two slides')
            missing=[s['slide_id'] for s in slides if not (state['reviews'].get(s['slide_id']) or {}).get('automatic_passed')]
            if missing:raise ValueError('Run check-slide successfully for every slide first: '+', '.join(missing))
            previous=state.get('deck_review') or {}
            if previous.get('status')=='pending' and previous.get('revision')==base_revision:return dict(previous,status='pending')
            state['deck_review']=dict(status='pending',revision=base_revision,request_id=hashlib.sha256(f'{state["presentation_id"]}:{base_revision}:{time.time()}'.encode()).hexdigest()[:16],
                requested_at=time.time(),slide_fingerprints={s['slide_id']:state['reviews'][s['slide_id']].get('fingerprint') for s in slides})
            return self.review_event(c,state,'presentation_deck_review_requested')
    def record_deck_review(self,request_id,result):
        with self.locked() as(c,state):
            deck=state.get('deck_review') or {}
            if deck.get('request_id')!=request_id or deck.get('status')!='pending':return dict(applied=False,reason='stale')
            state['deck_review']=dict(deck,**result)
            self.review_event(c,state,'presentation_deck_review')
            return dict(applied=True)
    def record_human_decision(self,revision,decision,notes,decision_id,slide_id=None,issue_ids=()):
        """Host only: an explicit, recorded human acceptance, rejection or issue dismissal."""
        if decision not in ('accept','reject','dismiss_issues'):raise ValueError('Unknown human decision')
        notes=notes if isinstance(notes,str) else ''
        if decision=='reject' and not notes.strip():raise ValueError('判定不通过时请填写修改意见')
        with self.locked() as(c,state):
            self.assert_current(state,revision)
            record=dict(decision=decision,notes=notes.strip()[:2000],decision_id=decision_id,decided_at=time.time(),reviewer='human',issue_ids=list(issue_ids))
            if slide_id is None:
                deck=state.get('deck_review')
                if not deck or deck.get('status')=='pending' and decision=='dismiss_issues':raise ValueError('No deck review to decide on')
                target=deck
            else:
                review=state.get('reviews',{}).get(slide_id)
                if not review or not review.get('automatic_passed'):raise ValueError('Human visual acceptance requires a successful formal check')
                formal=state.get('renders',{}).get(slide_id)
                if not formal or formal.get('revision',-1)<review.get('revision',0):raise ValueError('Formal screenshot of the checked revision is missing')
                target=review.setdefault('aesthetic_review',dict(status='pending'))
            if decision=='dismiss_issues':
                known={issue['id'] for issue in target.get('issues',[])}
                if not issue_ids or set(issue_ids)-known:raise ValueError('Dismiss existing issue ids only')
                for issue in target.get('issues',[]):
                    if issue['id'] in issue_ids:issue.update(dismissed=True,dismissed_by=decision_id)
                remaining=[issue for issue in target.get('issues',[]) if issue['severity'] in ('critical','major') and not issue.get('dismissed')]
                target.setdefault('dismissals',[]).append(record)
                if not remaining and target.get('status') in ('failed','uncertain'):target['human']=dict(record,decision='accept',via='dismissal')
            else:
                target['human']=record
            if slide_id is not None:
                review['status']=reviewer().derive_status(review);review['visual_review']=reviewer().compat_visual(review)
                review['visual_evidence']=dict(reviewer='human',notes=record['notes'])
            return self.review_event(c,state,'presentation_review' if slide_id else 'presentation_deck_review',slide_id,decision=record)
    def review(self,slide_id,revision,passed,notes,reviewer_kind='vision_model'):
        """Legacy entry point. Human: recorded decision. Agent: self-inspection notes only, never acceptance."""
        if type(passed)is not bool or not isinstance(notes,str) or not notes.strip():raise ValueError('Actual visual inspection and evidence required')
        if reviewer_kind=='human':
            return self.record_human_decision(revision,'accept' if passed else 'reject',notes,'legacy-'+hashlib.sha256(f'{slide_id}:{revision}:{time.time()}'.encode()).hexdigest()[:12],slide_id)
        if reviewer_kind!='vision_model':raise ValueError('Unknown reviewer')
        with self.locked() as(c,state):
            self.assert_current(state,revision);review=state['reviews'].get(slide_id)
            if not review or not review['automatic_passed'] or review['revision']>revision:raise ValueError('Exact-revision successful formal check required')
            review['agent_inspection']=dict(passed=passed,notes=notes.strip()[:2000],recorded_at=time.time(),
                note='Agent self-inspection is advisory; acceptance comes from the host vision review or a human decision')
            return self.review_event(c,state,'presentation_review',slide_id)
    def finalize(self,base_revision):
        with self.locked() as(c,state):
            self.assert_current(state,base_revision); prs=Presentation(io.BytesIO(self.snapshot_unlocked(c,base_revision)))
            if not len(prs.slides):raise ValueError('Empty presentation')
            # Delivery needs intact, content-checked slides. Visual review is advice and never holds it up.
            problems=[];aesthetics={}
            for slide in prs.slides:
                sid=renderer.slide_identity(slide);review=state['reviews'].get(sid,{})
                if not review.get('automatic_passed') or not review.get('content_notes'):problems.append(f'{sid}: automatic and content checks required');continue
                aesthetic=review.get('aesthetic_review') or {}
                aesthetics[sid]=dict(status=reviewer().effective_aesthetic(review),review_id=aesthetic.get('review_id'),
                    decision_id=(aesthetic.get('human') or {}).get('decision_id'),critical=[i['id'] for i in reviewer().outstanding_critical(review)],
                    suggestions=len([i for i in aesthetic.get('issues',[]) if not i.get('dismissed')]))
            if problems:raise ValueError('Finalize blocked: '+'; '.join(problems))
            validator.validate_package(self.snapshot_unlocked(c,base_revision)); state['finalized']=True
            outstanding={sid:item['critical'] for sid,item in aesthetics.items() if item['critical']}
            return self.emit_state(c,state,dict(type='presentation_finalized',presentation_id=state['presentation_id'],revision=base_revision,finalized=True,
                path=self.path.name,aesthetics=aesthetics,outstanding_critical=outstanding,deck_review_id=(state.get('deck_review') or {}).get('review_id'),
                note='Report outstanding critical findings and the number of suggestions to the user; they decide what to change.'))


CRITICAL_REPAIR_ROUNDS=2
_REVIEWER=None


def reviewer():
    global _REVIEWER
    if _REVIEWER is None:_REVIEWER=load('slide_reviewer')
    return _REVIEWER


def critical_issues(aesthetic):
    return [issue for issue in (aesthetic or {}).get('issues',[]) if issue.get('severity')=='critical' and issue.get('confidence') in ('high','medium')
            and not issue.get('needs_verification') and not issue.get('dismissed')]


def next_action(review):
    """Aesthetic review is advice: only critical findings are repaired unasked, nothing waits on it."""
    if not review:return 'Run check-slide for this slide.'
    if not review.get('automatic_passed'):return 'Fix the automatic check errors with atomic operations, then run check-slide again.'
    aesthetic=review.get('aesthetic_review') or {}
    status=reviewer().effective_aesthetic(review)
    critical=critical_issues(aesthetic) if (aesthetic.get('human') or {}).get('decision')!='accept' else []
    if critical and not aesthetic.get('auto_repair',True):
        return 'A critical issue survived '+str(CRITICAL_REPAIR_ROUNDS)+' repair rounds: stop editing it and tell the user; delivery is not blocked.'
    if critical:
        return 'Fix the critical issue(s) with small local edits, then run check-slide again. Do not act on other suggestions unless the user asks.'
    if status=='pending':return 'Vision review runs in the background; continue with the next slide, do not wait for it.'
    if status in ('unavailable','uncertain'):return 'No usable visual verdict; continue. Do not claim visual acceptance; mention it when you deliver.'
    if aesthetic.get('issues'):return 'No critical issue. Leave the suggestions to the user: list them briefly when you deliver and change nothing unless asked.'
    return 'No issues. Continue.'


def review_summary(slide_id,review):
    if not review:return dict(slide_id=slide_id,status='writing',next_action=next_action(None))
    aesthetic=review.get('aesthetic_review') or {}
    critical={issue['id'] for issue in critical_issues(aesthetic)}
    issues=aesthetic.get('issues') or []
    return dict(slide_id=slide_id,revision=review.get('revision'),status=review.get('status'),automatic_passed=review.get('automatic_passed'),
        technical_review=review.get('technical_review'),visual_integrity=review.get('visual_integrity'),
        aesthetic_review={key:aesthetic.get(key) for key in ('status','summary','issues','model_id','prompt_version','error','auto_repair','escalated_issues','human','review_id','reason') if key in aesthetic},
        critical_issues=[issue for issue in issues if issue['id'] in critical],
        suggestions=[{key:issue.get(key) for key in ('id','severity','category','element_ids','observation','suggestion')} for issue in issues if issue['id'] not in critical and not issue.get('dismissed')],
        effective_aesthetic=reviewer().effective_aesthetic(review),next_action=next_action(review))


def deck_summary(deck):
    return {key:deck.get(key) for key in ('status','revision','summary','issues','rejected_issues','model_id','prompt_version','error','human','review_id') if key in deck}


def _template_index(state):
    manifest=state.get('template_manifest') or {}
    if not manifest:return None
    return dict(template_id=manifest.get('template_id'),license=manifest.get('license'),
        slides=[dict(slide_id=slide.get('slide_id'),role=slide.get('role'),slots=slide.get('slots')) for slide in manifest.get('slides',[])])


def catalog():
    return dict(profiles=profiles().summary(),layouts=layouts().summary(),components=component_renderer.catalog(registry()),
        style_presets=native_style.catalog(),diagrams=diagram_presets.catalog())


def plan_request(store,request):
    profile_id=request.get('profile') or (store.status().get('profile_id') if store else None) or DEFAULT_PROFILE
    return layout_resolver.plan(profiles(),layouts(),profile_id,request.get('intent'),request.get('content'))


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('command',choices=['init','apply-one','status','restore','check-slide','review-slide','review-status','request-review','request-deck-review','finalize','profiles','layouts','components','catalog','plan','import-template','list-templates','match-template']); parser.add_argument('pptx',nargs='?'); parser.add_argument('--request'); parser.add_argument('--presentation-id'); parser.add_argument('--template'); parser.add_argument('--template-id'); parser.add_argument('--output'); parser.add_argument('--license-note',default=''); parser.add_argument('--role'); parser.add_argument('--profile'); parser.add_argument('--theme'); parser.add_argument('--use',choices=['edit_existing','template_reference'],default='edit_existing'); parser.add_argument('--slide-id'); parser.add_argument('--revision',type=int); parser.add_argument('--base-revision',type=int); parser.add_argument('--operation-id'); parser.add_argument('--notes',default=''); parser.add_argument('--intent'); parser.add_argument('--constraint',action='append',default=[]); parser.add_argument('--wait',type=float,default=0); parser.add_argument('--passed',action='store_true'); parser.add_argument('--reviewer',choices=['human','vision_model'],default='vision_model'); args=parser.parse_args()
    if args.command in ('profiles','layouts','components','catalog'):
        result=catalog() if args.command=='catalog' else {args.command:catalog()[args.command]}
        print(json.dumps(result,ensure_ascii=False)); return
    if args.command=='list-templates':
        print(json.dumps(template_importer.list_templates(),ensure_ascii=False)); return
    if args.command=='import-template':
        if not args.template:parser.error('--template is required')
        print(json.dumps(template_importer.import_template(args.template,args.template_id,args.output,args.license_note),ensure_ascii=False)); return
    if not args.pptx:parser.error('a presentation path is required')
    store=PresentationStore(args.pptx)
    if args.command=='plan':result=plan_request(store,json.loads(Path(args.request).read_text()))
    elif args.command=='init':result=store.init(args.presentation_id,args.template,theme=args.theme or args.profile or DEFAULT_PROFILE,profile=args.profile,use=args.use)
    elif args.command=='apply-one':result=store.apply_one(json.loads(Path(args.request).read_text()),Path(args.pptx).parent)
    elif args.command=='status':result=store.status()
    elif args.command=='restore':result=store.restore(args.revision,args.base_revision,args.operation_id)
    elif args.command=='check-slide':result=store.check_slide(args.slide_id,args.notes,args.intent,args.constraint)
    elif args.command=='review-slide':
        # Human decisions are recorded by the platform UI, never by a script in the sandbox.
        if args.reviewer=='human':raise ValueError('Human visual decisions are recorded in the platform preview, not from the sandbox')
        result=store.review(args.slide_id,args.revision,args.passed,args.notes,'vision_model')
    elif args.command=='review-status':result=store.review_status(args.slide_id,args.wait)
    elif args.command=='request-review':result=store.request_review(args.slide_id)
    elif args.command=='request-deck-review':result=store.request_deck_review(args.base_revision)
    elif args.command=='match-template':result=template_importer.match(store.status(),args.role)
    else:result=store.finalize(args.base_revision)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    try:main()
    except Exception as exc:print(json.dumps(dict(status='rejected',error=str(exc))),file=sys.stderr);sys.exit(1)
