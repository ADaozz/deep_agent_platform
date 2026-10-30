"""Deterministic PPTX package, geometry and conservative typography checks."""
import io
import math
from pathlib import Path
import posixpath
import subprocess
import zipfile
from lxml import etree


def validate_package(data):
    errors=[]; parsed={}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names=set(z.namelist())
        if len(names)!=len(z.namelist()) or len(names)>10000 or sum(i.file_size for i in z.infolist())>250*1024*1024:
            raise ValueError('Unsafe or oversized package')
        for name in names:
            if name.startswith('/') or '..' in Path(name).parts or '\\' in name:raise ValueError('Unsafe package path')
            if not name.endswith(('.xml','.rels')):continue
            raw=z.read(name)
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('DTD/ENTITY forbidden')
            root=etree.fromstring(raw,etree.XMLParser(resolve_entities=False,no_network=True));parsed[name]=root
            if name.endswith('.rels'):
                base='' if name=='_rels/.rels' else name.rsplit('/_rels/',1)[0]
                ids=set()
                for rel in root:
                    if rel.get('Id') in ids:errors.append('duplicate_relationship')
                    ids.add(rel.get('Id'))
                    if rel.get('TargetMode')=='External':continue
                    target=posixpath.normpath(posixpath.join(base,rel.get('Target','')))
                    if target.lstrip('/') not in names:errors.append('dangling_relationship:'+name)
        rel_namespace='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        for name,root in parsed.items():
            if name.endswith('.rels'):continue
            references={value for node in root.iter() for key,value in node.attrib.items() if key.startswith('{'+rel_namespace+'}')}
            if not references:continue
            folder,filename=posixpath.split(name);rels_name=posixpath.join(folder,'_rels',filename+'.rels')
            ids={node.get('Id') for node in parsed.get(rels_name,[])}
            if references-ids:errors.append('unresolved_reference:'+name)
        if 'ppt/presentation.xml' not in names or '[Content_Types].xml' not in names:errors.append('missing_package_part')
    if errors:raise ValueError(';'.join(errors))
    return True


def layout_issues(slide, width_points=960, height_points=540):
    """Geometry, proven overflow and collision checks. Every issue is an error."""
    issues=[]; elements=slide['elements']
    for element in elements:
        g=element['geometry']; x,y,w,h=(g[k] for k in ('x','y','w','h'))
        line=element['type']=='connector' or 'connector' in element
        if min(x,y)<-.001 or (max(w,h)<=0 if line else min(w,h)<=0) or x+w>1.001 or y+h>1.001:
            if element.get('layer') not in ('background','decoration'):issues.append({'type':'out_of_bounds','severity':'error','element_id':element['id']})
        blocks=element.get('text_blocks') if element['type']=='shape' else None
        if (element['type']=='text' and element.get('text')) or blocks:
            style=element['style']; font_name=style['font_family']
            measured=blocks or [dict(text=element['text'],font_size=style['font_size'],line_height=style.get('line_height',1.22))]
            try:
                from PIL import ImageFont
                result=subprocess.run(['fc-match','-f','%{file}\n%{family}',font_name],capture_output=True,text=True,timeout=5)
                lines=result.stdout.splitlines()
                if font_name.casefold() not in [f.strip().casefold() for f in lines[1].split(',')]:issues.append({'type':'missing_font','severity':'error','element_id':element['id'],'font':font_name})
                # Shape text sits inside 0.08 in side and 0.04 in vertical insets.
                inset_x,inset_y=(12,6) if blocks else (0,0)
                usable=max(1,w*width_points*2-12-inset_x*4)
                needed=0
                for block in measured:
                    size=block['font_size'];font=ImageFont.truetype(lines[0],max(1,round(size*2)))
                    count=sum(max(1,math.ceil(font.getlength(part)/usable)) for part in block['text'].split('\n'))
                    needed+=count*size*float(block.get('line_height',1.22) or 1.22)
                if needed>h*height_points-4-inset_y*2:issues.append({'type':'text_overflow','severity':'error','element_id':element['id']})
            except (OSError,IndexError,ValueError):issues.append({'type':'font_measurement_unavailable','severity':'error','element_id':element['id']})
    for i,a in enumerate(elements):
        if a.get('layer') in ('background','decoration') or a['type'] in ('complex','connector'):continue
        for b in elements[i+1:]:
            if b.get('layer') in ('background','decoration') or b['type'] in ('complex','connector'):continue
            if a.get('overlap_group') and a.get('overlap_group')==b.get('overlap_group'):continue
            x,y,w,h=(a['geometry'][k] for k in ('x','y','w','h')); X,Y,W,H=(b['geometry'][k] for k in ('x','y','w','h'))
            area=max(0,min(x+w,X+W)-max(x,X))*max(0,min(y+h,Y+H)-max(y,Y))
            if area>min(w*h,W*H)*.03:issues.append({'type':'unexpected_overlap','severity':'error','elements':[a['id'],b['id']]})
    return issues
