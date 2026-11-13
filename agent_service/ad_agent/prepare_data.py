"""Copy only business-query columns from an existing dump-derived SQLite snapshot."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from .config import ROOT

DEFAULT_SOURCE=Path(r'C:\Users\Administrator\Documents\Codex\2026-10-29\https-d8gspvluc6-feishu-cn-docx-uuy4dwnlmog91jx9bidcsus9nmd\outputs\数据阅读包\全部数据.sqlite')


def prepare(source, destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if source==destination or destination.exists():
        raise ValueError('Destination exists; choose a new output file to preserve existing data')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(destination)) as out:
        schemas={
          'materials':['id','advertiser_id','title','delivery_status','test_status','last_sync_at','report_last_sync_at'],
          'material_daily_trend_cache':['material_id','advertiser_id','date','total_cost','overall_gmv','overall_roi','synced_at'],
          'brand_account_bindings':['brand_id','advertiser_id'],
        }
        for table,columns in schemas.items():
            out.execute(f'CREATE TABLE {table} ('+','.join(f'{c} TEXT' for c in columns)+')')
            rows=src.execute(f'SELECT {",".join(columns)} FROM {table}')
            out.executemany(f'INSERT INTO {table} VALUES ({",".join("?" for _ in columns)})',rows)
        out.executescript('''CREATE INDEX material_scope ON materials(id,advertiser_id);
          CREATE INDEX daily_scope ON material_daily_trend_cache(material_id,advertiser_id,date);
          CREATE INDEX material_account ON materials(advertiser_id);''')
        candidate=out.execute('''SELECT b.brand_id,COUNT(*) AS n FROM brand_account_bindings b
          JOIN material_daily_trend_cache d ON d.advertiser_id=b.advertiser_id
          GROUP BY b.brand_id ORDER BY n DESC,b.brand_id LIMIT 1''').fetchone()
        if not candidate:
            raise ValueError('No verified brand/account binding with daily reports')
        accounts=[r[0] for r in out.execute('SELECT DISTINCT advertiser_id FROM brand_account_bindings WHERE brand_id=? ORDER BY advertiser_id',(candidate[0],))]
        coverage=out.execute('SELECT MIN(date),MAX(date),COUNT(*) FROM material_daily_trend_cache').fetchone()
        out.commit()
    config={'business_db':str(destination),'customer_id':'local-snapshot-viewer','brand_id':candidate[0],'accounts':accounts,'data_mode':'dump_snapshot','coverage':list(coverage),'source':'dump-derived curated SQLite','identity_mode':'local_snapshot_viewer_not_production_login'}
    config_path=destination.parent/'local-data.json'
    config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    return config_path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,default=DEFAULT_SOURCE)
    parser.add_argument('--output',type=Path,default=ROOT/'var/business_snapshot.sqlite')
    args=parser.parse_args()
    path=prepare(args.source,args.output)
    print('Prepared local query snapshot and configuration:',path)


if __name__=='__main__':main()
