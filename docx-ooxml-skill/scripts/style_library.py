"""Catalog paired design Markdown and reference page images. Not a DOCX template library."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'templates'
CP = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
DC = 'http://purl.org/dc/elements/1.1/'
CT = 'http://schemas.openxmlformats.org/officeDocument/2006/custom-properties'
VT = 'http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'


def _asset(root, value):
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Missing or unsafe style asset: ' + str(value))
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(root=ROOT):
    """Rewrite provenance hashes after a style file changes. Authoring only."""
    root = Path(root)
    for directory in sorted(p.parent for p in root.glob('*/*/design.md')):
        design, reference, parameters = directory / 'design.md', directory / 'reference.jpg', directory / 'parameters.json'
        source = dict(style_id=f'{directory.parent.name}/{directory.name}',
            design_sha256=_sha(design), reference_sha256=_sha(reference), parameters_sha256=_sha(parameters))
        (directory / 'source.json').write_text(json.dumps(source, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return build_catalog(root)


def build_catalog(root=ROOT):
    from PIL import Image
    root = Path(root)
    entries = []
    for provenance in sorted(root.glob('*/*/source.json')):
        directory = provenance.parent
        source = json.loads(provenance.read_text(encoding='utf-8'))
        category, theme = directory.parent.name, directory.name
        design = _asset(root, f'{category}/{theme}/design.md')
        reference = _asset(root, f'{category}/{theme}/reference.jpg')
        parameters = _asset(root, f'{category}/{theme}/parameters.json')
        if _sha(design) != source['design_sha256']:
            raise ValueError('Design Markdown was modified: ' + str(design))
        if _sha(reference) != source['reference_sha256']:
            raise ValueError('Reference JPEG does not match provenance: ' + str(reference))
        if _sha(parameters) != source['parameters_sha256']:
            raise ValueError('Style parameters were modified: ' + str(parameters))
        with Image.open(reference) as picture:
            picture.load()
            width, height = picture.size
            if picture.format != 'JPEG' or max(width, height) > 8192:
                raise ValueError('Invalid reference JPEG: ' + str(reference))
        headings = [line.lstrip('#').strip() for line in design.read_text(encoding='utf-8-sig').splitlines() if line.startswith('# ')]
        entries.append(dict(style_id=f'{category}/{theme}', category=category, theme_id=theme,
            title=headings[0] if headings else theme,
            design_md=f'{category}/{theme}/design.md', reference_jpg=f'{category}/{theme}/reference.jpg',
            parameters_json=f'{category}/{theme}/parameters.json',
            reference_width=width, reference_height=height))
    catalog = dict(schema='docx-style-library/v1', count=len(entries), styles=entries)
    (root / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return catalog


def list_styles(category=None, style_id=None, limit=None, root=ROOT):
    root = Path(root)
    path = root / 'catalog.json'
    if not path.is_file():
        return []
    result = []
    for entry in json.loads(path.read_text(encoding='utf-8'))['styles']:
        if category and entry['category'] != category:
            continue
        if style_id and entry['style_id'] != style_id:
            continue
        item = dict(entry, design_path=str(_asset(root, entry['design_md'])), reference_path=str(_asset(root, entry['reference_jpg'])))
        if style_id:
            item['parameters'] = json.loads(_asset(root, entry['parameters_json']).read_text(encoding='utf-8'))
        result.append(item)
    if limit is not None and not style_id:
        result = result[:limit]
    return result


def parameters(style_id, root=ROOT):
    matches = list_styles(style_id=style_id, root=root)
    if len(matches) != 1:
        raise ValueError('Unknown style library directory: ' + str(style_id))
    return matches[0]['parameters']


def reference(style_id, root=ROOT):
    if not isinstance(style_id, str):
        return None
    matches = list_styles(style_id=style_id, root=root)
    if len(matches) != 1:
        return None
    entry = matches[0]
    return dict(style_id=entry['style_id'], folder_name=Path(entry['design_md']).parent.name, title=entry['title'])


def _office():
    import importlib.util
    path = Path(__file__).with_name('office_docx.py')
    spec = importlib.util.spec_from_file_location('docx_style_office', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ensure_part(parts, name, data, content_type, rel_type, rel_id):
    office = _office()
    parts[name] = data if isinstance(data, bytes) else data.encode()
    content = office.parse_xml(parts[office.CT_FILE])
    part_name = '/' + name
    if not any(node.get('PartName') == part_name for node in content):
        office.etree.SubElement(content, office.q(office.CT, 'Override'), PartName=part_name, ContentType=content_type)
        parts[office.CT_FILE] = office.xml_bytes(content)
    rels = office.parse_xml(parts['_rels/.rels'])
    if not any(node.get('Id') == rel_id for node in rels):
        office.etree.SubElement(rels, office.q(office.RELS, 'Relationship'), Id=rel_id, Type=rel_type, Target=name)
        parts['_rels/.rels'] = office.xml_bytes(rels)


def apply_reference(parts, style_id, design=None, root=ROOT):
    """Record the style directory and the caller's design parameters. This does not typeset the document."""
    entry = reference(style_id, root=root)
    if entry is None:
        raise ValueError('Unknown style library directory: ' + str(style_id))
    office = _office()
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="{CP}" xmlns:dc="{DC}"><dc:title></dc:title><cp:keywords>docx-style:{entry["style_id"]}</cp:keywords></cp:coreProperties>'''
    _ensure_part(parts, 'docProps/core.xml', core,
        'application/vnd.openxmlformats-package.core-properties+xml',
        'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties', 'rIdCore')
    if design is not None:
        payload = json.dumps(design, ensure_ascii=False).replace('&', '&amp;').replace('<', '&lt;')
        custom = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="{CT}" xmlns:vt="{VT}"><property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="2" name="docx-design"><vt:lpwstr>{payload}</vt:lpwstr></property></Properties>'''
        _ensure_part(parts, 'docProps/custom.xml', custom,
            'application/vnd.openxmlformats-officedocument.custom-properties+xml',
            'http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties', 'rIdCustom')
    return entry


def read_reference(parts):
    if 'docProps/core.xml' not in parts:
        return None
    text = parts['docProps/core.xml'].decode('utf-8', 'replace')
    tags = re.findall(r'docx-style:([^\s<]+)', text)
    return reference(tags[-1]) if tags else None


def read_design(parts):
    if 'docProps/custom.xml' not in parts:
        return None
    office = _office()
    root = office.parse_xml(parts['docProps/custom.xml'])
    for node in root.iter(office.q(VT, 'lpwstr')):
        parent = node.getparent()
        if parent is not None and parent.get('name') == 'docx-design' and node.text:
            return json.loads(node.text)
    return None
