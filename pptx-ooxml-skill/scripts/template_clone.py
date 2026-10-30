"""Clone a slide without aliasing charts, embeddings or other mutable parts.

Sharing an image part is safe. Sharing a chart or its workbook is not: a later
``chart.replace_data`` would overwrite the source slide. Layouts and masters stay
shared so the clone keeps the template's theme and placeholders.
"""
import copy
import re

from lxml import etree
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.opc.package import XmlPart
from pptx.opc.packuri import PackURI
from pptx.oxml.ns import qn

REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
CHART_STYLE = 'http://schemas.microsoft.com/office/2011/relationships/chartStyle'
# These targets are immutable shared resources, or are already attached to the new slide.
SHARE = {
    RT.IMAGE, RT.SLIDE_LAYOUT, RT.SLIDE_MASTER, RT.THEME, RT.AUDIO, RT.VIDEO, RT.MEDIA,
    RT.CHART_COLOR_STYLE, CHART_STYLE,
}
SKIP_ON_SLIDE = {RT.SLIDE_LAYOUT, RT.NOTES_SLIDE}


def clone_slide(prs, source, new_slide_id):
    """Append a structural copy of ``source`` and return the new slide.

    The caller inserts it at the requested index and records stable state. The
    new slide's ``cSld/@name`` is ``da-slide:{new_slide_id}``. Shape names are
    copied, so slot ids such as ``title`` still resolve on the clone.
    """
    if not isinstance(new_slide_id, str) or not new_slide_id:
        raise ValueError('slide.clone requires a new slide_id')
    dest = prs.slides.add_slide(source.slide_layout)
    _clear_layout_shapes(dest)
    for shape in source.shapes:
        dest.shapes._spTree.append(copy.deepcopy(shape._element))
    _copy_slide_extras(source, dest)
    mapping = _clone_relationships(source, dest)
    missing = _relationship_ids(dest._element) - set(mapping)
    # Ids that already belonged to the destination (its layout relationship) are not in the map.
    dest_ids = {rel.rId for rel in dest.part.rels.values()}
    unresolved = {item for item in missing if item not in dest_ids}
    if unresolved:
        raise ValueError('Slide clone left unresolved relationships: ' + ', '.join(sorted(unresolved)))
    _rewrite_rel_ids(dest._element, mapping)
    dest._element.cSld.set('name', 'da-slide:' + new_slide_id)
    return dest


def _clear_layout_shapes(slide):
    tree = slide.shapes._spTree
    for child in list(tree):
        if etree.QName(child).localname in ('nvGrpSpPr', 'grpSpPr'):
            continue
        tree.remove(child)


def _copy_slide_extras(source, dest):
    for local in ('clrMapOvr', 'transition', 'timing'):
        node = source._element.find(qn(f'p:{local}'))
        if node is None:
            continue
        existing = dest._element.find(qn(f'p:{local}'))
        if existing is not None:
            dest._element.remove(existing)
        dest._element.append(copy.deepcopy(node))
    background = source._element.cSld.find(qn('p:bg'))
    if background is None:
        return
    existing = dest._element.cSld.find(qn('p:bg'))
    if existing is not None:
        dest._element.cSld.remove(existing)
    dest.shapes._spTree.addprevious(copy.deepcopy(background))


def _clone_relationships(source, dest):
    mapping = {}
    memo = {}
    reserved = set()
    package = source.part.package
    for rel in list(source.part.rels.values()):
        if rel.reltype in SKIP_ON_SLIDE:
            continue
        if rel.is_external:
            mapping[rel.rId] = dest.part.rels.get_or_add_ext_rel(rel.reltype, rel.target_ref)
            continue
        target = rel.target_part
        if rel.reltype not in SHARE:
            target = _duplicate_part(package, target, memo, reserved)
        mapping[rel.rId] = dest.part.relate_to(target, rel.reltype)
    return mapping


def _duplicate_part(package, part, memo, reserved):
    key = str(part.partname)
    if key in memo:
        return memo[key]
    name = _allocate(package, part.partname, reserved)
    try:
        if isinstance(part, XmlPart):
            created = type(part).load(name, part.content_type, package, part.blob)
        else:
            created = type(part)(name, part.content_type, package, part.blob)
    except Exception as exc:
        raise ValueError(f'Cannot safely clone package part {part.partname}') from exc
    memo[key] = created
    mapping = {}
    for rel in list(part.rels.values()):
        if rel.is_external:
            mapping[rel.rId] = created.rels.get_or_add_ext_rel(rel.reltype, rel.target_ref)
            continue
        target = rel.target_part
        if rel.reltype not in SHARE:
            target = _duplicate_part(package, target, memo, reserved)
        mapping[rel.rId] = created.relate_to(target, rel.reltype)
    if isinstance(created, XmlPart):
        _rewrite_rel_ids(created._element, mapping)
    return created


def _allocate(package, partname, reserved):
    template = _numbered_template(str(partname))
    existing = {str(part.partname) for part in package.iter_parts()}
    existing.update(reserved)
    for number in range(1, 10001):
        candidate = template % number
        if candidate not in existing:
            reserved.add(candidate)
            return PackURI(candidate)
    raise ValueError('Could not allocate a package part name')


def _numbered_template(partname):
    replaced, count = re.subn(r'(\d+)(\.[A-Za-z0-9]+)$', r'%d\2', partname)
    if count == 1:
        return replaced
    stem, dot, ext = partname.rpartition('.')
    return stem + '%d.' + ext


def _relationship_ids(element):
    found = set()
    prefix = '{' + REL_NS + '}'
    for node in element.iter():
        for attr, value in node.attrib.items():
            if attr.startswith(prefix):
                found.add(value)
    return found


def _rewrite_rel_ids(element, mapping):
    prefix = '{' + REL_NS + '}'
    for node in element.iter():
        for attr, value in list(node.attrib.items()):
            if attr.startswith(prefix) and value in mapping:
                node.set(attr, mapping[value])
