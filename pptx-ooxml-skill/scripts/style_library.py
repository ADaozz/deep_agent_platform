"""Catalog paired design Markdown and local reference images; no PPTX imports."""
import hashlib
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'templates'


def _asset(root,value):
    path=(root/value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():raise ValueError('Missing or unsafe style asset: '+str(value))
    return path


def build_catalog(root=ROOT):
    from PIL import Image
    root=Path(root);entries=[]
    for provenance in sorted(root.glob('*/*/source.json')):
        directory=provenance.parent;source=json.loads(provenance.read_text())
        category,theme=directory.parent.name,directory.name
        design=_asset(root,f'{category}/{theme}/design.md')
        reference=_asset(root,f'{category}/{theme}/reference.jpg')
        if hashlib.sha256(design.read_bytes()).hexdigest()!=source['original_design_sha256']:
            raise ValueError('Design Markdown was modified: '+str(design))
        if source.get('reference_sha256') and hashlib.sha256(reference.read_bytes()).hexdigest()!=source['reference_sha256']:
            raise ValueError('Reference JPEG does not match provenance: '+str(reference))
        with Image.open(reference) as picture:
            picture.load();width,height=picture.size
            if picture.format!='JPEG' or max(width,height)>8192:raise ValueError('Invalid reference JPEG: '+str(reference))
        headings=[line.lstrip('#').strip() for line in design.read_text(encoding='utf-8-sig').splitlines() if line.startswith('# ')]
        entries.append(dict(style_id=f'{category}/{theme}',category=category,theme_id=theme,
            title=headings[0] if headings else theme,
            design_md=f'{category}/{theme}/design.md',reference_jpg=f'{category}/{theme}/reference.jpg',
            reference_width=width,reference_height=height,provenance=source.get('provenance'),
            removed_slides=source.get('removed_slides',0)))
    catalog=dict(schema='pptx-style-library/v1',count=len(entries),styles=entries)
    (root/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n')
    return catalog


def list_styles(category=None,style_id=None,root=ROOT):
    root=Path(root);path=root/'catalog.json'
    if not path.is_file():return []
    entries=json.loads(path.read_text())['styles']
    result=[]
    for entry in entries:
        if category and entry['category']!=category:continue
        if style_id and entry['style_id']!=style_id:continue
        result.append(dict(entry,design_path=str(_asset(root,entry['design_md'])),reference_path=str(_asset(root,entry['reference_jpg']))))
    return result


def reference(style_id,root=ROOT):
    """Resolve a real library directory, never a profile's display name."""
    if not isinstance(style_id,str):return None
    matches=list_styles(style_id=style_id,root=root)
    if len(matches)!=1:return None
    entry=matches[0]
    return dict(style_id=entry['style_id'],folder_name=Path(entry['design_md']).parent.name)


def presentation_reference(prs,root=ROOT):
    tags=re.findall(r'(?:^|[;\s])pptx-style:([^;\s]+)',prs.core_properties.keywords or '')
    return reference(tags[-1],root=root) if tags else None


def apply_reference(prs,style_id,root=ROOT,*,design=None):
    entry=reference(style_id,root=root)
    if entry is None:raise ValueError('Unknown style library directory: '+str(style_id))
    previous=re.sub(r'(?:^|[;\s])pptx-style:[^;\s]+','',prs.core_properties.keywords or '').strip(' ;')
    prs.core_properties.keywords=(previous+'; ' if previous else '')+'pptx-style:'+entry['style_id']
    if design is not None:
        import review_design
        review_design.embed(prs,design)
    return entry
