"""Style references and explicit unsupported-chart fallbacks stay auditable."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE
from pptx.util import Inches

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
spec=importlib.util.spec_from_file_location('style_library_engine',SCRIPTS/'office_pptx.py')
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)


def add_style(root,category,theme='shared-theme'):
    folder=root/category/theme;folder.mkdir(parents=True)
    md=b'# Example design\n\nUse blue accents.\n';(folder/'design.md').write_bytes(md)
    Image.new('RGB',(160,90),'white').save(folder/'reference.jpg','JPEG')
    (folder/'source.json').write_text(json.dumps(dict(original_design_sha256=hashlib.sha256(md).hexdigest(),provenance='original')))
    return folder


def test_catalog_preserves_category_variants_and_asset_bytes(tmp_path):
    first=add_style(tmp_path,'consulting');add_style(tmp_path,'01_strategy')
    before=(first/'design.md').read_bytes()
    catalog=engine.style_library.build_catalog(tmp_path)
    assert catalog['count']==2
    assert {s['style_id'] for s in catalog['styles']}=={'consulting/shared-theme','01_strategy/shared-theme'}
    found=engine.style_library.list_styles(category='consulting',root=tmp_path)
    assert len(found)==1 and Path(found[0]['design_path']).read_bytes()==before
    assert len(engine.style_library.list_styles(style_id='01_strategy/shared-theme',root=tmp_path))==1
    assert engine.style_library.list_styles(style_id='../escape',root=tmp_path)==[]
    assert (first/'design.md').read_bytes()==before


def test_catalog_rejects_modified_markdown_or_missing_reference(tmp_path):
    folder=add_style(tmp_path,'work');(folder/'design.md').write_text('rewritten')
    with pytest.raises(ValueError,match='modified'):engine.style_library.build_catalog(tmp_path)
    (folder/'reference.jpg').unlink()
    with pytest.raises(ValueError,match='Missing'):engine.style_library.build_catalog(tmp_path)


def test_style_reference_survives_save_and_preserves_other_keywords(tmp_path):
    add_style(tmp_path,'consulting');engine.style_library.build_catalog(tmp_path)
    prs=Presentation();prs.core_properties.keywords='report; quarterly'
    expected={'style_id':'consulting/shared-theme','folder_name':'shared-theme'}
    assert engine.style_library.apply_reference(prs,expected['style_id'],root=tmp_path)==expected
    engine.style_library.apply_reference(prs,expected['style_id'],root=tmp_path)
    output=tmp_path/'deck.pptx';prs.save(output)
    restored=Presentation(output)
    assert engine.style_library.presentation_reference(restored,root=tmp_path)==expected
    assert restored.core_properties.keywords.count('pptx-style:')==1
    assert 'report; quarterly' in restored.core_properties.keywords
    with pytest.raises(ValueError,match='Unknown style'):
        engine.style_library.apply_reference(prs,'missing/theme',root=tmp_path)


def test_plan_style_requires_unambiguous_output_and_real_directory(tmp_path):
    files=tmp_path/'files';files.mkdir();(tmp_path/'tmp').mkdir()
    first=files/'deck.pptx';first.touch()
    plan_path=tmp_path/'tmp'/'deck-plan.json'
    plan_path.write_text(json.dumps({'style':'consulting/red-black-growth'}))
    store=engine.PresentationStore(first)
    expected={'style_id':'consulting/red-black-growth','folder_name':'red-black-growth'}
    assert store.plan_style_reference()==expected
    second=files/'other.pptx';second.touch()
    assert store.plan_style_reference() is None
    plan_path.write_text(json.dumps({'style_id':expected['style_id'],'pptx':'files/deck.pptx'}))
    assert store.plan_style_reference()==expected
    assert engine.PresentationStore(second).plan_style_reference() is None
    plan_path.write_text(json.dumps({'style_id':'missing/theme','pptx':'files/deck.pptx'}))
    assert store.plan_style_reference() is None


def test_manifest_uses_embedded_style_and_never_default_profile_label(tmp_path):
    path=tmp_path/'deck.pptx'
    store=engine.PresentationStore(path)
    event=store.init('style-test',style_id='consulting/red-black-growth')
    expected={'style_id':'consulting/red-black-growth','folder_name':'red-black-growth'}
    assert event['manifest']['style_reference']==expected
    assert store.status()['manifest']['style_reference']==expected
    prs=Presentation(path)
    assert engine.renderer.manifest(prs,{'style_reference':{'folder_name':'incorrect'}})['style_reference']==expected
    assert engine.renderer.manifest(Presentation(),{})['style_reference'] is None


def scene():
    prs=Presentation();slide=prs.slides.add_slide(prs.slide_layouts[6])
    shape=slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE,Inches(1),Inches(1),Inches(2),Inches(2));shape.name='da:sankey-flow'
    return prs,slide


def plan(kind='sankey',reason='Native engine lacks this chart type',ids=None):
    return {'slides':[{'page':1,'statistical_charts':1,'chart_fallbacks':[{'type':kind,'reason':reason,'element_ids':ids if ids is not None else ['sankey-flow']}]}]}


def test_explicit_unsupported_fallback_passes_without_claiming_all_native():
    prs,_=scene();result=engine.native_charts.audit(prs,plan())
    assert result['status']=='passed' and result['native_charts']==0 and result['fallback_charts']==1
    assert result['all_native'] is False
    assert engine.native_charts.audit(prs)['status']=='inventory'


@pytest.mark.parametrize('payload',[
    plan('line'),plan('waterfall','LibreOffice cannot preview it'),plan(reason=''),
    plan(ids=['missing']),plan(ids=['sankey-flow','sankey-flow']),
])
def test_invalid_or_unnecessary_chart_fallback_is_rejected(payload):
    prs,_=scene();assert engine.native_charts.audit(prs,payload)['status']=='failed'


def test_native_chart_cannot_also_be_counted_as_a_fallback():
    prs,slide=scene()
    chart={'type':'column_clustered','categories':['A','B'],'series':[{'name':'Revenue','values':[1,2]}]}
    shape=engine.native_charts.create(slide,chart,(Inches(4),Inches(1),Inches(5),Inches(4)));shape.name='da:data'
    assert engine.native_charts.audit(prs,plan(ids=['data']))['status']=='failed'
    both=plan();both['slides'][0]['statistical_charts']=2
    result=engine.native_charts.audit(prs,both)
    assert result['status']=='passed' and result['native_charts']==1 and result['fallback_charts']==1


def test_one_shape_cannot_stand_for_two_fallback_charts():
    prs,_=scene();payload=plan();payload['slides'][0]['statistical_charts']=2
    payload['slides'][0]['chart_fallbacks'].append(dict(type='chord',reason='Unsupported',element_ids=['da:sankey-flow']))
    result=engine.native_charts.audit(prs,payload)
    assert result['status']=='failed' and any(i['type']=='reused_fallback_element' for i in result['issues'])
