#!/usr/bin/env python3
"""Real formatted DOCX corpus, structural assertions, render timing and QA state.

Does not rubber-stamp visual review. Run with LibreOffice, fontconfig and the
fonts in tests/docx_corpus.py installed. Output contains screenshots for review.
"""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from platform_app.format_plugins import load_plugin
_plugin = load_plugin('docx')
Documents, engine = _plugin.Documents, _plugin.engine
spec = importlib.util.spec_from_file_location('docx_corpus', ROOT / 'tests' / 'docx_corpus.py')
corpus = importlib.util.module_from_spec(spec); spec.loader.exec_module(corpus)


def percentiles(values):
    values = sorted(values)
    return {f'p{p}_ms': round(values[max(0, math.ceil(len(values)*p/100)-1)]*1000, 2) for p in (50,95)} if values else {}


def check_structure(store):
    parts = engine.o.read_parts(store.path)
    root = engine.o.parse_xml(parts[engine.o.MAIN]); ns = engine.o.NS
    assert root.xpath('string(//w:sectPr/w:pgSz/@w:w)', namespaces=ns) == '11906'
    assert root.xpath('string(//w:sectPr/w:pgSz/@w:h)', namespaces=ns) == '16838'
    assert all(root.xpath(f'string(//w:sectPr/w:pgMar/@w:{side})', namespaces=ns) == '1440' for side in ('top','bottom','left','right'))
    assert not root.xpath('//w:br[@w:type="page"]|//w:pageBreakBefore', namespaces=ns)
    for p in root.iter(engine.o.q(engine.o.W,'p')):
        assert len(engine.o.ptext(p)) <= 180
    return {'a4': True, 'margins': True, 'short_paragraphs': True, 'no_forced_page_breaks': True, 'ooxml_valid': engine.o.validate_parts(parts)['ok']}


def run_case(output, name, sections, images, tables, template=False):
    root = output / name; root.mkdir(parents=True)
    corpus.make_assets(root)
    notices = []; timings = []; render_times = []
    docs = Documents(root, lambda event: notices.append((time.time(), event)))
    source = corpus.make_template(root) if template else None
    store = docs.store(root / 'report.docx')
    store.init('report', source)
    docs.sync()
    for operation in corpus.formatted_operations(sections, images, tables):
        state = store.status()
        if state['gate'] != 'open':
            start = time.perf_counter(); review = store.checkpoint(); docs.sync(); render_times.append(time.perf_counter()-start)
            if review['gate'] != 'open':
                raise ValueError(f'{name}: checkpoint failed at {state["revision"]}: {json.dumps(review, ensure_ascii=False)}')
        start = time.perf_counter()
        store.apply_one(dict(document_id='report', operation_id=f'op-{state["revision"]+1}', base_revision=state['revision'], operation=operation))
        docs.sync()
        timings.append(time.perf_counter()-start)
    start=time.perf_counter(); final=store.checkpoint(final=True); docs.sync(); render_times.append(time.perf_counter()-start)
    checks=check_structure(store) if not template else {'ooxml_valid': engine.o.validate_parts(engine.o.read_parts(store.path))['ok'], 'template_header_preserved': engine.o.read_parts(source)['word/header1.xml']==engine.o.read_parts(store.path)['word/header1.xml']}
    assert all(checks.values()), checks
    assert final['gate']=='open', final
    pages=final['pages']
    assert all(p['checks']['visual_review']=='pending' for p in pages)
    # Engine observer latency, measured separately from HTTP/SSE delivery.
    observe=[seen-event['committed_at'] for seen,event in notices if 'committed_at' in event]
    return dict(case=name, revisions=store.status()['revision'], page_count=len(pages), checks=checks,
        commit=percentiles(timings), commit_to_journal_callback=percentiles(observe), checkpoint=percentiles(render_times),
        visual_review='pending', finalized=False, render_directory=str(store.render_directory(store.status()['revision'])))


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); parser.add_argument('--case',default='all',choices=['all','report','table','image','template','long'])
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    cases=[('report',3,False,False,False),('table',3,False,True,False),('image',3,True,False,False),('template',3,True,True,True),('long',12,True,True,False)]
    results=[]
    for name,sections,images,tables,template in cases:
        if args.case not in ('all',name):continue
        result=run_case(args.output,name,sections,images,tables,template)
        results.append(result); print(json.dumps(result,ensure_ascii=False),flush=True)
        if name=='long' and result['page_count']<10:
            raise ValueError('Long fixture must actually render at least 10 pages')
    (args.output/'results.json').write_text(json.dumps(dict(format_requirements=corpus.FORMAT_REQUIREMENTS,results=results),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
