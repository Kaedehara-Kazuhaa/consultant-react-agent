import argparse
import asyncio
from datetime import datetime
import json
import sys
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
from .app import build_engine
from .config import Settings
from .models import Request
from .tools import Context

def main():
    # Keep Chinese CLI output readable under Windows redirected output as well.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("message")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--session", default="cli", help="Stable conversation thread ID")
    parser.add_argument("--export", type=Path, help="Export redacted trace as JSONL")
    args = parser.parse_args()
    engine = build_engine(Settings.from_env())
    context = Context("demo-operator", "B1", ("A1",), datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(), args.session)
    request = Request(message=args.message, request_id=uuid4().hex, session_id=args.session)
    result = asyncio.run(engine.run(request, context))
    events = engine.store.events(request.request_id, "demo-operator:B1")
    if args.trace:
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events)+"\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
