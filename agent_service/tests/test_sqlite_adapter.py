import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
from ad_agent.config import ROOT
from ad_agent.tools import BusinessTools, Context, Daily, Material


def test_readonly_snapshot_account_scope_and_duplicate_guard(tmp_path):
    path=tmp_path/'business.sqlite'
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
          CREATE TABLE materials (id TEXT, advertiser_id TEXT,title TEXT,delivery_status TEXT,test_status TEXT,last_sync_at TEXT,report_last_sync_at TEXT);
          CREATE TABLE material_daily_trend_cache (material_id TEXT,advertiser_id TEXT,date TEXT,total_cost TEXT,overall_gmv TEXT,synced_at TEXT);
          INSERT INTO materials VALUES ('M0001','A1','Scoped','active','passed','2026-03-24','2026-03-24');
          INSERT INTO materials VALUES ('M0002','A2','Other brand','active','passed','2026-03-24','2026-03-24');
          INSERT INTO material_daily_trend_cache VALUES ('M0001','A1','2026-03-24','100','300','2026-03-24');
          INSERT INTO material_daily_trend_cache VALUES ('M0001','A2','2026-03-24','900','9999','2026-03-24');
        ''')
        db.commit()
    before=path.read_bytes()
    tools=BusinessTools(ROOT/'fixtures/business.json',ROOT/'profiles/default/knowledge',str(path))
    ctx=Context('u','B1',('A1',),'2026-03-26','s')
    obs=asyncio.run(tools.daily(Daily(material_id='M0001',start_date='2026-03-24',end_date='2026-03-24'),ctx))
    assert obs.data['roi']=='3.00'
    assert obs.data['synthetic'] is False
    assert path.read_bytes()==before
    assert asyncio.run(tools.resolve(Material(material_id='M0002'),ctx)).status=='EMPTY'
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO material_daily_trend_cache VALUES ('M0001','A1','2026-03-24','100','300','2026-03-24')")
        db.commit()
    assert asyncio.run(tools.daily(Daily(material_id='M0001',start_date='2026-03-24',end_date='2026-03-24'),ctx)).error_code=='DUPLICATE_DAILY_KEY'
