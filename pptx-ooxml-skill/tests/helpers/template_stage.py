"""Internal-navigation fixture preparation; not a template-library entrypoint."""
import re
import shutil
import zipfile
from lxml import etree
A="{http://schemas.openxmlformats.org/drawingml/2006/main}"
R="{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

def stage(source, target):
    """Keep the original; remove only cross-slide navigation from an import copy."""
    changes = {}
    removed = 0
    with zipfile.ZipFile(source) as package:
        for name in package.namelist():
            if not re.fullmatch(r'ppt/slides/_rels/slide\d+\.xml.rels', name):
                continue
            rels = etree.fromstring(package.read(name))
            ids = {rel.get('Id') for rel in rels if rel.get('Type', '').endswith('/slide')}
            if not ids:
                continue
            slide_name = name.replace('/_rels/', '/').removesuffix('.rels')
            slide = etree.fromstring(package.read(slide_name))
            for node in list(slide.iter()):
                if node.tag in (A + 'hlinkClick', A + 'hlinkHover') and node.get(R + 'id') in ids:
                    node.getparent().remove(node)
                    removed += 1
            for rel in list(rels):
                if rel.get('Id') in ids:
                    rels.remove(rel)
            changes[name] = etree.tostring(rels, xml_declaration=True, encoding='UTF-8', standalone=True)
            changes[slide_name] = etree.tostring(slide, xml_declaration=True, encoding='UTF-8', standalone=True)
        if not changes:
            shutil.copyfile(source, target)
        else:
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as output:
                for entry in package.infolist():
                    output.writestr(entry, changes.get(entry.filename, package.read(entry.filename)))
    return removed
