"""Selected styles survive file copies and reach numeric and visual review."""
import copy
import importlib.util
import io
import json
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('review_design_engine',ROOT/'scripts/office_pptx.py')
engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
design=engine.review_design
STYLE='consulting/red-black-growth'


def make_prs(parameters=None):
    prs=Presentation();prs.slide_width=Inches(10);prs.slide_height=Inches(7.5)
    s=prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_textbox(Inches(1),Inches(1),Inches(5),Inches(1)).text='Selected design'
    engine.style_library.apply_reference(prs,STYLE,design=parameters)
    return prs


def test_embedded_parameters_win_over_plan_and_follow_file_copy(tmp_path):
    params={'palette':{'accent':'308050','text':'222222'},'fonts':{'family':'Noto Sans CJK SC'},
            'user_requirements':['用户要求绿色'], 'user_overrides':{'palette':{'accent':'D06020'}}}
    prs=make_prs(params);before=prs.slides[0]._element.xml
    out=io.BytesIO();prs.save(out);copy_prs=Presentation(io.BytesIO(out.getvalue()))
    resolved=design.resolve(copy_prs,plan={'style_id':STYLE,'palette':{'accent':'246BCE'}})
    assert resolved['style_id']==STYLE and resolved['design_md'].startswith('#')
    assert resolved['parameters']==params
    assert design.profile(resolved,copy_prs)['colors']['accent']=='D06020'
    assert copy_prs.slides[0]._element.xml==before
    design.embed(copy_prs,{'palette':{'accent':'112233'}})
    assert design.embedded(copy_prs)=={'palette':{'accent':'112233'}}
    assert len([rel for rel in copy_prs.part.rels.values() if rel.reltype==engine.RT.CUSTOM_XML])==1


def test_parameters_of_a_different_style_or_plan_are_not_reused():
    prs=make_prs({'palette':{'accent':'308050'}})
    engine.style_library.apply_reference(prs,'academic/paper-white-courseware')
    assert design.embedded(prs) is None
    resolved=design.resolve(prs,plan={'style_id':STYLE,'palette':{'accent':'246BCE'}})
    assert resolved['parameters']=={} and resolved['style_id']=='academic/paper-white-courseware'


def test_only_declared_margin_units_are_used_and_declared_font_pairs_are_valid():
    prs=make_prs();params={'fonts':{'title':'Arial','body':'Noto Sans CJK SC'},
        'margins':{'unit':'in','left':.4,'right':.5,'top':.3,'bottom':.4}}
    resolved=design.resolve(prs,plan=dict(style_id=STYLE,**params));profile=design.profile(resolved,prs)
    assert profile['spacing']['margin_left']==.04 and profile['spacing']['margin_right']==.05
    slide={'elements':[{'id':key,'type':'text','source':'generated','text':'Readable',
        'geometry':{'x':.043,'y':.043+i*.2,'w':.5,'h':.1},'style':{'font_size':20,'font_family':family,'color':'17324D'}}
        for i,(key,family) in enumerate([('title','Arial'),('body','Noto Sans CJK SC')])],'background':'FFFFFF'}
    issues=engine.design_quality.review(slide,profile)
    assert not any(i['type'] in ('safe_area_violation','mixed_font_family') for i in issues)
    params['margins'].pop('unit')
    profile=design.profile(design.resolve(prs,plan=dict(style_id=STYLE,**params)),prs)
    assert profile['quality']['safe_area'] is False
    assert not any(i['type']=='safe_area_violation' for i in engine.design_quality.review(slide,profile))
    slide['elements'][0]['geometry']['x']=-.1
    assert any(i['type']=='out_of_bounds' for i in engine.validator.layout_issues(slide))


def test_manifest_snapshot_and_both_model_requests_use_real_design(tmp_path):
    path=tmp_path/'deck.pptx';prs=make_prs({'palette':{'accent':'308050'},'margins':{'unit':'fraction','left':.04,'right':.04,'top':.04,'bottom':.04}});prs.save(path)
    store=engine.PresentationStore(path);store.track_external('deck')
    manifest=store.status()['manifest']
    assert manifest['profile']['id']=='corporate_blue'  # Internal generation fallback only.
    assert manifest['review_profile']['id']==STYLE
    assert manifest['review_profile']['colors']['accent']=='308050'
    spec=importlib.util.spec_from_file_location('selected_design_vision',ROOT/'vision_review_service.py')
    vision=importlib.util.module_from_spec(spec);spec.loader.exec_module(vision)
    context={'profile':manifest['review_profile'],'design':manifest['review_design']}
    page=json.loads(vision.slide_request(dict(context,slide=manifest['slides'][0],index=0,total=1)))
    deck=json.loads(vision.deck_request(dict(context,slides=[])))
    for request in (page,deck):
        assert request['selected_design']['style_id']==STYLE
        assert request['selected_design']['design_md']
        assert request['design_profile']['colors']['accent']=='308050'


def test_plan_context_is_captured_and_local_atomic_edits_do_not_adopt_new_plan(tmp_path):
    files=tmp_path/'files';files.mkdir();tmp=tmp_path/'tmp';tmp.mkdir()
    plan=tmp/'deck-plan.json';plan.write_text(json.dumps({'style_id':STYLE,'pptx':'files/deck.pptx','palette':{'accent':'308050'}}))
    path=files/'deck.pptx';make_prs().save(path)
    store=engine.PresentationStore(path);store.track_external('deck');first=store.status()['manifest']['review_design']
    plan.write_text(json.dumps({'style_id':STYLE,'pptx':'files/deck.pptx','palette':{'accent':'246BCE'}}))
    sid=store.status()['manifest']['slides'][0]['slide_id']
    store.apply_one(dict(presentation_id='deck',operation_id='move',base_revision=0,operation='element.adjust',slide_id=sid,
        element={'id':'template-2','dx':.01}))
    assert store.status()['manifest']['review_design']['sha256']==first['sha256']


def test_existing_ppt_without_selected_style_does_not_receive_blue_requirements(tmp_path):
    path=tmp_path/'existing.pptx';prs=Presentation();prs.slides.add_slide(prs.slide_layouts[6]);prs.save(path)
    store=engine.PresentationStore(path);store.track_external('deck');manifest=store.status()['manifest']
    assert manifest['review_design']['source']=='existing_ppt'
    assert manifest['review_profile']['colors']=={} and not manifest['review_profile']['quality']['safe_area']
