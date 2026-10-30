"""Build the original business-report DOCX used to exercise template reuse.

This file is project content. It is not a Microsoft, SlidesCarnival or third-party
template, and nothing here downloads or vendors one.
"""
import importlib.util
import json
from pathlib import Path

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
RELS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
ACCENT = '1F4E79'


def build(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    office = _load('office_docx')
    atomic = _load('atomic_docx')
    importer = _load('template_importer')
    parts = _parts()
    result = office.validate_parts(parts)
    if not result['ok']:
        raise RuntimeError(result['errors'])
    parts = atomic.mark_blocks(parts)
    package = directory / 'template.docx'
    office.save_parts(parts, package)
    manifest = importer.analyze(parts, office, 'business-report', source=str(package))
    manifest['template_id'] = 'business-report'
    manifest['license'] = dict(
        status='recorded',
        redistributable=True,
        note='Original project template created for technical verification. Not a third-party design.',
    )
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'styles.json').write_text(json.dumps(dict(styles=manifest['styles'], tokens=manifest['tokens']), ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'LICENSE.txt').write_text(
        'business-report is original content of this repository.\n'
        'It may be stored and reused inside this project.\n'
        'Third-party templates still need their own redistribution license before they are added here.\n',
        encoding='utf-8')
    return package


def _parts():
    body = [
        _sdt('project_title', 'Title', '项目总结'),
        _paragraph('Heading1', '项目背景', bold=True),
        _paragraph('Normal', '本段保留模板正文样式，用于复制章节。'),
        _paragraph('Normal', '第二段说明范围、进度和需要沿用的表格样式。'),
        _paragraph('Heading1', '进度', bold=True),
        _table(),
        _paragraph('Heading1', '篇幅', bold=True),
    ]
    sentence = '模板章节沿用标题样式、正文样式和页眉页脚，由排版引擎重新分页，而不是把每一页画成图片。' * 12
    body.extend(_paragraph('Normal', sentence) for _ in range(8))
    body.append(_paragraph('Heading1', '结尾', bold=True))
    body.append(_paragraph('Normal', '例外色', color='FF00AA'))
    document = f'''<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>
      {''.join(body)}
      <w:sectPr>
        <w:headerReference w:type="default" r:id="rId2"/>
        <w:footerReference w:type="default" r:id="rId3"/>
        <w:pgSz w:w="11906" w:h="16838"/>
        <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>
      </w:sectPr>
    </w:body></w:document>'''
    return {
        '[Content_Types].xml': f'''<Types xmlns="{CT}">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
          <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
          <Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>
          <Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
          <Override PartName="/word/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
        </Types>'''.encode(),
        '_rels/.rels': f'<Relationships xmlns="{RELS}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>'.encode(),
        'word/_rels/document.xml.rels': f'''<Relationships xmlns="{RELS}">
          <Relationship Id="rId1" Type="{R}/styles" Target="styles.xml"/>
          <Relationship Id="rId2" Type="{R}/header" Target="header1.xml"/>
          <Relationship Id="rId3" Type="{R}/footer" Target="footer1.xml"/>
          <Relationship Id="rId4" Type="{R}/theme" Target="theme/theme1.xml"/>
        </Relationships>'''.encode(),
        'word/document.xml': document.encode(),
        'word/styles.xml': _styles().encode(),
        'word/header1.xml': _story('hdr', 'Header', '内部项目报告').encode(),
        'word/footer1.xml': _story('ftr', 'Footer', '机密').encode(),
        'word/theme/theme1.xml': _theme().encode(),
    }


def _paragraph(style, text, bold=False, color=None):
    props = ''
    if bold or color:
        color_xml = f'<w:color w:val="{color}"/>' if color else ''
        bold_xml = '<w:b/>' if bold else ''
        props = f'<w:rPr>{bold_xml}{color_xml}</w:rPr>'
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r>{props}<w:t>{text}</w:t></w:r></w:p>'


def _sdt(tag, style, text):
    return f'''<w:sdt><w:sdtPr><w:tag w:val="{tag}"/></w:sdtPr><w:sdtContent>
      {_paragraph(style, text, bold=True)}
    </w:sdtContent></w:sdt>'''


def _table():
    def cell(text, header=False):
        shade = '<w:shd w:val="clear" w:fill="F2F4F7"/>' if header else ''
        bold = '<w:rPr><w:b/></w:rPr>' if header else '<w:rPr/>'
        return f'<w:tc><w:tcPr><w:tcW w:w="4500" w:type="dxa"/>{shade}</w:tcPr><w:p><w:r>{bold}<w:t>{text}</w:t></w:r></w:p></w:tc>'
    header = ''.join(cell(text, True) for text in ('事项', '状态'))
    body = ''.join(cell(text) for text in ('范围', '沿用模板'))
    return f'''<w:tbl>
      <w:tblPr><w:tblW w:w="9000" w:type="dxa"/><w:tblBorders>
        <w:top w:val="single" w:sz="4" w:color="BCC4CC"/><w:left w:val="single" w:sz="4" w:color="BCC4CC"/>
        <w:bottom w:val="single" w:sz="4" w:color="BCC4CC"/><w:right w:val="single" w:sz="4" w:color="BCC4CC"/>
        <w:insideH w:val="single" w:sz="4" w:color="BCC4CC"/><w:insideV w:val="single" w:sz="4" w:color="BCC4CC"/>
      </w:tblBorders></w:tblPr>
      <w:tblGrid><w:gridCol w:w="4500"/><w:gridCol w:w="4500"/></w:tblGrid>
      <w:tr><w:trPr><w:tblHeader/></w:trPr>{header}</w:tr>
      <w:tr>{body}</w:tr>
    </w:tbl>'''


def _story(tag, style, text):
    return f'<w:{tag} xmlns:w="{W}"><w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p></w:{tag}>'


def _styles():
    def style(style_id, name, size, bold=False, color=None, align=None):
        color_xml = f'<w:color w:val="{color}" w:themeColor="accent1"/>' if color else ''
        bold_xml = '<w:b/>' if bold else ''
        align_xml = f'<w:jc w:val="{align}"/>' if align else ''
        return f'''<w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{name}"/>
          <w:basedOn w:val="Normal"/><w:pPr>{align_xml}</w:pPr><w:rPr>{bold_xml}<w:sz w:val="{size}"/>{color_xml}</w:rPr></w:style>'''
    return f'''<w:styles xmlns:w="{W}">
      <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Liberation Sans" w:hAnsi="Liberation Sans" w:eastAsia="Noto Sans CJK SC"/>
        <w:sz w:val="21"/></w:rPr></w:rPrDefault></w:docDefaults>
      <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>
        <w:pPr><w:spacing w:after="160" w:line="360" w:lineRule="auto"/></w:pPr></w:style>
      {style('Title', 'Title', 40, True, ACCENT, 'center')}
      {style('Heading1', 'heading 1', 32, True, ACCENT)}
      {style('Heading2', 'heading 2', 26, True, ACCENT)}
      {style('Header', 'header', 18, False, None)}
      {style('Footer', 'footer', 18, False, None)}
    </w:styles>'''


def _theme():
    def slot(name, color):
        return f'<a:{name}><a:srgbClr val="{color}"/></a:{name}>'
    return f'''<a:theme xmlns:a="{A}" name="Business"><a:themeElements>
      <a:clrScheme name="Business">
        {slot('dk1', '20242A')}{slot('lt1', 'FFFFFF')}{slot('dk2', '5C6B7A')}{slot('lt2', 'F4F6F8')}
        {slot('accent1', ACCENT)}{slot('accent2', '5C6B7A')}{slot('accent3', ACCENT)}
        {slot('accent4', ACCENT)}{slot('accent5', ACCENT)}{slot('accent6', ACCENT)}
        {slot('hlink', ACCENT)}{slot('folHlink', '5C6B7A')}
      </a:clrScheme>
      <a:fontScheme name="Business"><a:majorFont><a:latin typeface="Liberation Sans"/><a:ea typeface="Noto Sans CJK SC"/></a:majorFont>
        <a:minorFont><a:latin typeface="Liberation Sans"/><a:ea typeface="Noto Sans CJK SC"/></a:minorFont></a:fontScheme>
      <a:fmtScheme name="Business"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>
        <a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
        <a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
        <a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>
    </a:themeElements></a:theme>'''


def _load(name):
    path = Path(__file__).with_name(name + '.py')
    spec = importlib.util.spec_from_file_location('docx_sample_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == '__main__':
    print(build(Path(__file__).resolve().parents[1] / 'templates' / 'business-report'))
