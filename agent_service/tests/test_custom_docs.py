import asyncio
from dataclasses import replace
import shutil
from uuid import uuid4
from ad_agent.config import ROOT, Settings
from ad_agent.engine import Engine
from ad_agent.models import Decision, Request
from ad_agent.tools import BusinessTools, Context


def test_custom_documents_reach_decision_provider(tmp_path):
    profile = tmp_path/'custom'
    shutil.copytree(ROOT/'profiles/default',profile)
    doc = profile/'skills/material/SKILL.md'
    doc.write_text(doc.read_text(encoding='utf-8')+'\nCUSTOM_SKILL_MARKER',encoding='utf-8')
    settings = Settings(profile_dir=profile,trace_db=tmp_path/'runtime.sqlite')
    class Capture:
        async def decide(self,state):
            assert '品牌方投手' in state['agent_document']
            assert any('CUSTOM_SKILL_MARKER' in s['instructions'] for s in state['skills'])
            assert 'get_material_status' in {t['name'] for t in state['tools']}
            return Decision(action_type='clarify',decision_summary='条件不全，先明确需求。',response='请明确日期。')
    engine=Engine(settings,BusinessTools(ROOT/'fixtures/business.json',profile/'knowledge'),Capture())
    req=Request(request_id=uuid4().hex,message='素材查询',session_id='s')
    result=asyncio.run(engine.run(req,Context('u','B1',('A1',),'2026-11-12','s')))
    assert result['status']=='COLLECTING'
