"""Offline synthetic chain acceptance. No model key or business snapshot is loaded."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from uuid import uuid4
from .app import build_engine
from .config import ROOT, Settings
from .models import Request
from .tools import Context


async def evaluate(report_path=None):
    scenarios=json.loads((ROOT/'evals/scenarios.json').read_text(encoding='utf-8'))
    checks=[]
    with tempfile.TemporaryDirectory() as folder:
        settings=Settings(trace_db=Path(folder)/'eval.sqlite')
        engine=build_engine(settings)
        for case in scenarios:
            session=uuid4().hex
            for index, expected in enumerate(case['turns'],1):
                request=Request(request_id=uuid4().hex,message=expected['message'],session_id=session)
                result=await engine.run(request,Context('eval','B1',('A1',),'2026-03-26',session))
                events=engine.store.events(request.request_id,'eval:B1')
                tools=[e['payload']['tool'] for e in events if e['type']=='action']
                errors=[]
                if result['status'] != expected['status']:
                    errors.append(f"status:{result['status']} != {expected['status']}")
                if tools != expected['tools']:
                    errors.append(f"tools:{tools} != {expected['tools']}")
                for word in expected.get('contains',[]):
                    if word not in result['response']:
                        errors.append(f'missing text:{word}')
                if events[-1]['type']!='run_finished':
                    errors.append('trace not finished')
                checks.append({'scenario':case['name'],'turn':index,'message':expected['message'],'passed':not errors,'errors':errors,'status':result['status'],'tools':tools,'trace_events':len(events)})
    report={'mode':'deterministic_demo','data':'synthetic','business_day':'2026-03-26','generated_at':datetime.now(timezone.utc).isoformat(),'scenarios':len(scenarios),'turns':len(checks),'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks),'checks':checks}
    path=Path(report_path or ROOT/'var/eval_report.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    report=asyncio.run(evaluate())
    print(json.dumps({k:v for k,v in report.items() if k!='checks'},ensure_ascii=False,indent=2))
    for check in report['checks']:
        if not check['passed']:
            print(json.dumps(check,ensure_ascii=False))
    raise SystemExit(1 if report['failed'] else 0)


if __name__=='__main__':
    main()
