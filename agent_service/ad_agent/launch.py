"""Daily local launcher: never installs packages or rebuilds frontend implicitly."""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from .config import ROOT


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',choices=['auto','dump','synthetic'],default='auto')
    parser.add_argument('--provider',choices=['demo','compatible','codex'],default=os.getenv('AGENT_PROVIDER','demo'))
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    local=ROOT/'var/local-data.json'
    mode='dump' if args.data=='auto' and local.exists() else 'synthetic' if args.data=='auto' else args.data
    if mode=='dump':
        if not local.exists():
            raise SystemExit('请先运行 python -m ad_agent.prepare_data --source 你的SQLite路径，或使用 --data synthetic。')
        os.environ['AGENT_LOCAL_DATA_CONFIG']=str(local)
    else:
        os.environ.pop('AGENT_LOCAL_DATA_CONFIG',None)
        os.environ.pop('AGENT_BUSINESS_DB',None)
    os.environ['AGENT_PROVIDER']=args.provider
    os.environ['AGENT_TRACE_DB']=str(ROOT/f'var/runtime-{mode}-{args.provider}.sqlite')
    if args.provider=='codex':
        executable=shutil.which(os.getenv('CODEX_EXECUTABLE','codex'))
        if not executable:raise SystemExit('未找到官方Codex CLI。')
        try:
            status=subprocess.run([executable,'login','status'],capture_output=True,timeout=10)
        except subprocess.TimeoutExpired:raise SystemExit('Codex登录状态检查超时，请先在终端运行 codex login status。')
        if status.returncode:raise SystemExit('Codex CLI尚未登录。先运行 codex login，完成你的账号授权后，再启动项目 --provider codex。此方式使用你自己的账号额度，不复用当前聊天会话。')
    if not (ROOT/'frontend/dist/index.html').exists():
        raise SystemExit('前端尚未构建，请首次运行 Setup.cmd；日常启动不会重新安装。')
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',args.port))==0:
            raise SystemExit(f'端口{args.port}已被占用。请关闭旧启动窗口，或使用 --port 8766。')
    print(f'数据：{mode}；决策器：{args.provider}。此启动不执行pip/npm安装。')
    url=f'http://127.0.0.1:{args.port}'
    if not args.no_browser:
        def open_when_ready():
            import time
            for _ in range(40):
                with socket.socket() as s:
                    if s.connect_ex(('127.0.0.1',args.port))==0:
                        webbrowser.open(url)
                        return
                time.sleep(.25)
        threading.Thread(target=open_when_ready,daemon=True).start()
    import uvicorn
    uvicorn.run('ad_agent.app:create_app',factory=True,host='127.0.0.1',port=args.port)


if __name__=='__main__':main()
