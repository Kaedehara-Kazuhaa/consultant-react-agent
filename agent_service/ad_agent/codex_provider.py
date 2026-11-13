"""Optional LOCAL prototype adapter using the user's own official CLI login."""
import asyncio
import json
from pathlib import Path
import shutil
import tempfile
from .models import Decision
from .providers import SYSTEM


class CodexProvider:
    def __init__(self,settings):
        self.executable=shutil.which(settings.codex_executable)
        if not self.executable:
            raise ValueError('Codex CLI not installed')

    async def decide(self,state):
        # CLI handles its own credentials. Never read auth.json or extract access tokens.
        with tempfile.TemporaryDirectory(prefix='consultant-decision-') as folder:
            root=Path(folder)
            schema=root/'decision.schema.json'
            output=root/'decision.json'
            properties={'action_type':{'type':'string','enum':['tool','answer','clarify','unresolved']},'decision_summary':{'type':'string'},'tool_name':{'type':['string','null']},'arguments_json':{'type':'string'},'response':{'type':['string','null']},'evidence_refs':{'type':'array','items':{'type':'string'}}}
            schema.write_text(json.dumps({'type':'object','additionalProperties':False,'properties':properties,'required':list(properties)}),encoding='utf-8')
            proc=await asyncio.create_subprocess_exec(self.executable,'exec','--ignore-user-config','--skip-git-repo-check','--ephemeral','--sandbox','read-only','-c','features.shell_tool=false','--output-schema',str(schema),'--output-last-message',str(output),'-C',folder,'-',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            prompt=SYSTEM+'\n只对给定JSON状态返回Decision，不使用任何CLI工具，不读取文件。工具参数放在arguments_json中，内容为JSON对象字符串；无参数填{}。\n'+json.dumps(state,ensure_ascii=False)
            try:
                await proc.communicate(prompt.encode('utf-8'))
            except BaseException:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                raise
            if proc.returncode or not output.exists() or output.stat().st_size>64000:
                raise ValueError('Codex decision unavailable; check codex login status and account quota')
            value=json.loads(output.read_text(encoding='utf-8'))
            value['arguments']=json.loads(value.pop('arguments_json'))
            return Decision.model_validate(value)
