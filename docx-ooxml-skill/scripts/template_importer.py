"""Describe a DOCX template's styles, content controls and cloneable sections.

The importer records a license note and does not copy an external file into
the skill. Content controls are the preferred slots; paragraphs that already
carry stable block ids are listed for templates that have none.
"""
import json
import shutil
import subprocess
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]


def load_office():
    import importlib.util
    path = Path(__file__).with_name('office_docx.py')
    spec = importlib.util.spec_from_file_location('docx_template_office', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def import_template(path, template_id=None, output=None, license_note=''):
    source = Path(path)
    if not source.is_file():
        raise ValueError('Template file not found')
    office = load_office()
    parts = office.read_parts(source)
    manifest = analyze(parts, office, template_id or source.stem, source=str(source), license_note=license_note)
    if output:
        dest = Path(output)
        if dest.suffix.lower() != '.json':
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / 'manifest.json'
        _refuse_unlicensed_vendor(dest, manifest)
        dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        manifest = dict(manifest, manifest_path=str(dest))
    return manifest


def analyze(parts, office, template_id='template', source='', license_note=''):
    root = office.parse_xml(parts[office.MAIN])
    styles = _styles(parts, office)
    controls = _controls(root, office)
    blocks = []
    for node in root.iter():
        block_id = node.get('{urn:deep-agent:docx:blocks:v1}id')
        if not block_id or node.tag not in (office.q(office.W, 'p'), office.q(office.W, 'tbl')):
            continue
        style = None
        style_node = node.find('./' + office.q(office.W, 'pPr') + '/' + office.q(office.W, 'pStyle'))
        if style_node is not None:
            style = style_node.get(office.q(office.W, 'val'))
        blocks.append(dict(
            block_id=block_id,
            type='table' if node.tag == office.q(office.W, 'tbl') else 'paragraph',
            style=style,
            text=office.ptext(node)[:180],
            heading=_heading(style),
        ))
    fonts = _fonts(parts, office)
    installed = _installed_families()
    missing = sorted(face for face in fonts if face.casefold() not in installed and not face.startswith('+'))
    warnings = []
    if missing:
        warnings.append('missing fonts: ' + ', '.join(missing))
    if shutil.which('libreoffice') is None and shutil.which('soffice') is None:
        warnings.append('LibreOffice is not available to verify pagination')
    sections = [block for block in blocks if block['heading']]
    tables = [block for block in blocks if block['type'] == 'table']
    return dict(
        schema='deep-agent-template/v1',
        kind='docx',
        template_id=template_id,
        source=source,
        license=_license(source, license_note),
        styles=styles,
        tokens=_tokens(parts, office),
        content_controls=controls,
        sections=[{'block_id': block['block_id'], 'style': block['style'], 'text': block['text']} for block in sections],
        tables=[{'block_id': block['block_id'], 'text': block['text']} for block in tables],
        headers=sorted(name for name in parts if name.startswith('word/header')),
        footers=sorted(name for name in parts if name.startswith('word/footer')),
        precheck=dict(blocks=len(blocks), headings=len(sections), tables=len(tables),
            content_controls=len(controls), fonts=fonts, missing_fonts=missing, warnings=warnings),
        blocks=blocks,
    )


def _styles(parts, office):
    if 'word/styles.xml' not in parts:
        return []
    root = office.parse_xml(parts['word/styles.xml'])
    found = []
    for style in root.iter(office.q(office.W, 'style')):
        found.append(dict(style_id=style.get(office.q(office.W, 'styleId')), type=style.get(office.q(office.W, 'type'))))
    return found


def _controls(root, office):
    found = []
    for sdt in root.iter(office.q(office.W, 'sdt')):
        tag = sdt.find('.//' + office.q(office.W, 'tag'))
        if tag is None:
            continue
        found.append(dict(tag=tag.get(office.q(office.W, 'val')), text=office.ptext(sdt)[:180]))
    return found


def _heading(style):
    return bool(style) and str(style).lower().startswith('heading')


def _tokens(parts, office):
    theme = parts.get('word/theme/theme1.xml')
    if not theme:
        return {}
    root = office.parse_xml(theme)
    namespace = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
    scheme = root.find('.//' + namespace + 'clrScheme')
    tokens = {}
    if scheme is None:
        return tokens
    for child in scheme:
        srgb = child.find(namespace + 'srgbClr')
        if srgb is not None and srgb.get('val'):
            tokens[child.tag.rsplit('}', 1)[-1]] = srgb.get('val').upper()
    return tokens


def _fonts(parts, office):
    faces = []
    for name in ('word/styles.xml', 'word/theme/theme1.xml'):
        if name not in parts:
            continue
        root = office.parse_xml(parts[name])
        for node in root.iter():
            for attr, value in node.attrib.items():
                if attr.endswith('}ascii') or attr.endswith('}eastAsia') or attr.endswith('}hAnsi') or attr.endswith('typeface'):
                    if value and value not in faces:
                        faces.append(value)
    return faces


def _installed_families():
    if shutil.which('fc-list') is None:
        return set()
    try:
        result = subprocess.run(['fc-list', ':family'], capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {part.strip().casefold() for line in result.stdout.splitlines() for part in line.split(',')}


def _license(source, note):
    return dict(
        status='recorded' if note else 'external',
        redistributable=False,
        note=note or 'External file. Do not copy it into the skill unless redistribution is explicitly allowed.',
        source=source,
    )


def _refuse_unlicensed_vendor(dest, manifest):
    root = (SKILL_ROOT / 'templates').resolve()
    target = Path(dest).resolve()
    if target == root or root in target.parents:
        if not (manifest.get('license') or {}).get('redistributable'):
            raise ValueError('Refusing to write an unlicensed template manifest into the skill templates directory')
