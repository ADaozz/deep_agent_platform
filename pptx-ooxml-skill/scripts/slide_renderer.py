"""Structured editable-element preview. Complex template objects stay opaque."""
import base64
import json
from pptx.enum.shapes import MSO_SHAPE_TYPE


def slide_identity(slide):
    name=slide._element.cSld.get('name','')
    return name[9:] if name.startswith('da-slide:') else f'template-{slide.slide_id}'


def element_identity(shape):
    return shape.name[3:] if shape.name.startswith('da:') else f'template-{shape.shape_id}'


def metadata(shape):
    nodes=shape._element.xpath('.//p:cNvPr')
    if nodes:
        try:return json.loads(nodes[0].get('descr','{}'))
        except (ValueError,TypeError):pass
    return {}


def set_metadata(shape,data):
    shape._element.xpath('.//p:cNvPr')[0].set('descr',json.dumps(data,ensure_ascii=False))


def manifest(prs,state):
    result=[]; width,height=prs.slide_width,prs.slide_height
    for slide in prs.slides:
        sid=slide_identity(slide); elements=[]; complex_slide=bool(state.get('template_complex',{}).get(sid))
        for shape in slide.shapes:
            meta=metadata(shape)
            g={k:round(float(v),6) for k,v in zip(('x','y','w','h'),(shape.left/width,shape.top/height,shape.width/width,shape.height/height))}
            e=dict(id=element_identity(shape),geometry=g,layer=meta.get('layer','content'),anchor=meta.get('anchor'),overlap_group=meta.get('overlap_group'),style=meta.get('resolved_style',dict(font_family='Noto Sans CJK SC',font_size=20,color='17324D')))
            if meta.get('kind'):
                e['type']=meta['kind']
                for key in ('text','shape','rows','chart','fill','line','alt'):
                    if key in meta:e[key]=meta[key]
                if e['type']=='image':e['image_type']=shape.image.content_type
                if e['type']=='chart' and (e['chart'].get('type')=='pie' or any(v<0 for series in e['chart']['series'] for v in series['values'])):complex_slide=True
            elif shape.has_text_frame:
                # An untouched template's rich text/theme can be more complicated
                # than the quick renderer. Use the actual rendered slide instead.
                e.update(type='complex',text=shape.text); complex_slide=True
            else:
                e.update(type='complex'); complex_slide=True
            elements.append(e)
        result.append(dict(slide_id=sid,elements=elements,complex=complex_slide,
            background=state.get('backgrounds',{}).get(sid,'FFFFFF'),layout=state.get('layouts',{}).get(sid,'title_body'),
            review=state.get('reviews',{}).get(sid,dict(status='writing',visual_review='pending')),
            formal=state.get('renders',{}).get(sid)))
    return dict(width=width,height=height,aspect_ratio=width/height,slides=result)
