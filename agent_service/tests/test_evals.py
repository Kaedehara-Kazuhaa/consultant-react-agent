import asyncio
from ad_agent.evals import evaluate


def test_chain_acceptance(tmp_path):
    report=asyncio.run(evaluate(tmp_path/'report.json'))
    assert report['scenarios']==10
    assert report['failed']==0, [c for c in report['checks'] if not c['passed']]
