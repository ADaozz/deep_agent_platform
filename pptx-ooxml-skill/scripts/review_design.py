"""Snapshot the selected design for review without restyling the presentation."""
import copy
import hashlib
import json
import math
from lxml import etree
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import Part
import style_library

NS='urn:pptx-ooxml-author:review-design:v1'
FIELDS=('fonts','font_family','palette','theme','type_scale','margins','footer','density','charts','tables','user_requirements','user_overrides')


def parameters(value):
    if not isinstance(value,dict):raise ValueError('Design parameters must be an object')
    result={key:copy.deepcopy(value[key]) for key in FIELDS if key in value}
    if len(json.dumps(result,ensure_ascii=False,allow_nan=False))>24000:raise ValueError('Design parameters exceed 24000 characters')
    return result


def embed(prs,value):
    """Keep explicit design parameters with the PPT, including after export/copy."""
    payload=parameters(value)
    element=etree.Element('{'+NS+'}reviewDesign');element.text=json.dumps(payload,ensure_ascii=False,allow_nan=False)
    element.set('style_id',(style_library.presentation_reference(prs) or {}).get('style_id',''))
    existing=None
    for rel in prs.part.rels.values():
        if rel.reltype!=RT.CUSTOM_XML or rel.is_external:continue
        try:root=etree.fromstring(rel.target_part.blob,etree.XMLParser(resolve_entities=False,no_network=True))
        except etree.XMLSyntaxError:continue
        if root.tag==element.tag:existing=rel;break
    if existing is not None:prs.part.drop_rel(existing.rId)
    part=Part(prs.part.package.next_partname('/customXml/item%d.xml'),'application/xml',prs.part.package,
              etree.tostring(element,encoding='UTF-8',xml_declaration=True))
    prs.part.relate_to(part,RT.CUSTOM_XML)


def embedded(prs):
    for rel in prs.part.rels.values():
        if rel.reltype!=RT.CUSTOM_XML or rel.is_external:continue
        try:
            root=etree.fromstring(rel.target_part.blob,etree.XMLParser(resolve_entities=False,no_network=True))
            if root.tag=='{'+NS+'}reviewDesign' and root.get('style_id','')==(style_library.presentation_reference(prs) or {}).get('style_id',''):
                return parameters(json.loads(root.text or '{}'))
        except (ValueError,TypeError,etree.XMLSyntaxError):continue
    return None


def resolve(prs,reference=None,plan=None,previous=None,existing_ppt=False):
    """A committed file owns its embedded design; a matching plan is the fallback."""
    reference=style_library.presentation_reference(prs) or reference
    plan=plan or {}
    if reference and (plan.get('style_id') or plan.get('style')) not in (None,reference['style_id']):plan={}
    explicit=embedded(prs)
    params=explicit if explicit is not None else parameters(plan) if plan else None
    if params is None and previous and previous.get('style_id')==(reference or {}).get('style_id'):
        params=copy.deepcopy(previous.get('parameters') or {})
    if not reference and not params and not existing_ppt:return None
    result=dict(source='style_library' if reference else 'explicit_design' if params else 'existing_ppt',
                parameters=params or {},precedence=['user_requirements/user_overrides','parameters','design_md','existing_ppt'])
    if reference:
        entry=style_library.list_styles(style_id=reference['style_id'])[0]
        from pathlib import Path
        path=Path(entry['design_path'])
        if path.stat().st_size>64000:raise ValueError('Style Markdown exceeds 64000 bytes')
        raw=path.read_bytes();text=raw.decode('utf-8-sig')
        result.update(reference,design_md=text,design_md_sha256=hashlib.sha256(raw).hexdigest())
    result['sha256']=hashlib.sha256(json.dumps(result,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return result


def profile(design,prs):
    """Only explicit numeric choices become rules; unknown MD values stay visual."""
    if not design:return None
    p=design.get('parameters') or {}
    overrides=p.get('user_overrides')
    if isinstance(overrides,dict):p={**p,**parameters(overrides)}
    colors=p.get('palette') or p.get('theme') or {}
    colors={k:v.lstrip('#').upper() for k,v in colors.items() if isinstance(v,str) and len(v.lstrip('#'))==6 and all(c in '0123456789abcdefABCDEF' for c in v.lstrip('#'))} if isinstance(colors,dict) else {}
    fonts=p.get('fonts') or {};fonts=dict(fonts) if isinstance(fonts,dict) else {'family':fonts} if isinstance(fonts,str) else {}
    family=fonts.get('body') or fonts.get('family') or p.get('font_family')
    if family:fonts.setdefault('body',family)
    if isinstance(fonts.get('fallback'),str):fonts['fallback']=[fonts['fallback']]
    sizes={}
    for key,value in (p.get('type_scale') or {}).items() if isinstance(p.get('type_scale'),dict) else []:
        size=value.get('size') if isinstance(value,dict) else value
        if type(size) in (int,float) and math.isfinite(size) and 0<size<=144:sizes[key]={'size':size}
    spacing={};margins=p.get('margins') or {}
    unit=margins.get('unit') if isinstance(margins,dict) else None
    if unit in ('fraction','in','pt'):
        for side,dimension in (('left',prs.slide_width),('right',prs.slide_width),('top',prs.slide_height),('bottom',prs.slide_height)):
            value=margins.get(side)
            if type(value) not in (int,float) or not math.isfinite(value):continue
            value=value if unit=='fraction' else value/(dimension/914400) if unit=='in' else value/(dimension/12700)
            if 0<=value<.5:spacing['margin_'+side]=value
    safe=all('margin_'+side in spacing for side in ('left','right','top','bottom'))
    return dict(id=design.get('style_id') or design['source'],name=design.get('folder_name') or design['source'],
        description='Actual selected design; internal fallback profile is not an acceptance criterion.',colors=colors,fonts=fonts,
        type_scale=sizes,spacing=spacing,quality={'safe_area':safe,'min_font_pt':8},
        density=p.get('density'),charts=p.get('charts') or {},tables=p.get('tables') or {})
