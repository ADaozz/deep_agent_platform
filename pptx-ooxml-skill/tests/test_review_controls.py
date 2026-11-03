"""Manual verdicts and per-slide reruns cannot be invoked through old APIs."""
import asyncio
import importlib.util
from pathlib import Path

from fastapi import FastAPI
import httpx
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('review_controls_adapter',ROOT/'platform_adapter.py')
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)


def test_removed_controls_are_rejected_before_reaching_storage():
    class Host:
        def service(self,*args):raise AssertionError('Removed actions must not reach storage')
    app=FastAPI();app.include_router(adapter.create_router(Host()))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            prefix='/api/agent/sessions/s/presentations/p'
            response=await client.post(prefix+'/review',json={'revision':0,'slide_id':'p1','passed':True})
            assert response.status_code==404
            for decision in ('accept','reject'):
                response=await client.post(prefix+'/aesthetic-decision',json={'revision':0,'slide_id':'p1','decision':decision})
                assert response.status_code==422
            response=await client.post(prefix+'/aesthetic-review',json={'revision':0,'slide_id':'p1'})
            assert response.status_code==422
    asyncio.run(check())


def test_historical_human_flags_do_not_replace_model_results():
    reviewer=adapter.engine.reviewer()
    critical=dict(id='issue',severity='critical',confidence='high')
    review=dict(automatic_passed=True,aesthetic_review={'status':'failed','issues':[critical],'human':{'decision':'accept'}})
    assert reviewer.effective_aesthetic(review)=='failed'
    assert reviewer.outstanding_critical(review)==[critical]
    assert reviewer.derive_status(review)=='needs_attention'
    review['aesthetic_review']={'status':'passed','issues':[],'human':{'decision':'reject'}}
    assert reviewer.effective_aesthetic(review)=='passed'
    assert reviewer.derive_status(review)=='passed'


def test_ledger_only_records_issue_dismissals(tmp_path):
    ledger=adapter.vision.ReviewLedger(tmp_path/'reviews.sqlite')
    for decision in ('accept','reject'):
        with pytest.raises(ValueError,match='no longer supported'):ledger.decide(decision=decision)
    identity=ledger.decide(decision='dismiss_issues',issue_ids=['issue'])
    assert ledger.decision(identity)['decision']=='dismiss_issues'
