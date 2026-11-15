from dataclasses import dataclass, field
from pathlib import Path
import os
import json

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    profile_dir: Path = ROOT / "profiles/default"
    trace_db: Path = ROOT / "var/runtime.sqlite"
    provider: str = "demo"
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="",repr=False)
    api_token: str = field(default="",repr=False)
    local_browser_auth: bool = False
    model_thinking: str = ""
    model_max_tokens: int = 2048
    test_mode: bool = False
    business_db: str = ""
    customer_context_file: str = ""
    customer_id: str = "demo-operator"
    brand_id: str = "B1"
    accounts: tuple[str, ...] = ("A1",)
    codex_executable: str = "codex"
    max_actions: int = 6
    call_timeout: float = 30
    turn_timeout: float = 90
    retries: int = 1
    silence_seconds: int = 1800
    handoff_history_days: int = 30
    handoff_history_limit: int = 3
    handoff_attempts: int = 3

    def __post_init__(self):
        if not 1 <= self.max_actions <= 20 or not 0 <= self.retries <= 1:
            raise ValueError("Invalid execution budget")
        if not 0 < self.call_timeout <= self.turn_timeout:
            raise ValueError("Invalid timeout budget")
        if min(self.silence_seconds,self.handoff_history_days,self.handoff_history_limit,self.handoff_attempts)<1:
            raise ValueError('Support policy values must be positive')
        if self.model_thinking not in {'','disabled','enabled'} or not 1<=self.model_max_tokens<=32768:
            raise ValueError('Invalid model generation settings')

    @classmethod
    def from_env(cls):
        from .local_config import model_config,read_secret
        model_local,config_path=model_config()
        provider=os.getenv('AGENT_PROVIDER',model_local.get('provider','demo'))
        def secret(env_name,file_key):
            if env_name in os.environ:
                return os.environ[env_name]
            filename=model_local.get(file_key)
            return read_secret(config_path,filename) if provider=='compatible' and filename else ''
        local={}
        if os.getenv('AGENT_LOCAL_DATA_CONFIG'):
            local=json.loads(Path(os.environ['AGENT_LOCAL_DATA_CONFIG']).read_text(encoding='utf-8'))
        return cls(
            profile_dir=Path(os.getenv("AGENT_PROFILE_DIR", str(ROOT / "profiles/default"))),
            trace_db=Path(os.getenv("AGENT_TRACE_DB", str(ROOT / "var/runtime.sqlite"))),
            provider=provider,
            base_url=os.getenv("MODEL_BASE_URL",model_local.get('base_url','')), model=os.getenv("MODEL_NAME",model_local.get('model','')),
            api_key=secret('MODEL_API_KEY','key_file'),api_token=secret('AGENT_API_TOKEN','token_file'),
            local_browser_auth=model_local.get('local_browser_auth',False) is True,
            test_mode=os.getenv('AGENT_TEST_MODE','1' if model_local.get('test_mode') is True else '0')=='1',
            model_thinking=os.getenv('MODEL_THINKING',model_local.get('thinking','')),
            model_max_tokens=int(os.getenv('MODEL_MAX_TOKENS',str(model_local.get('max_tokens',2048)))),
            business_db=os.getenv("AGENT_BUSINESS_DB", local.get('business_db','')),
            customer_context_file=os.getenv("AGENT_CUSTOMER_CONTEXT_FILE", ""),
            customer_id=local.get('customer_id','demo-operator'),brand_id=local.get('brand_id','B1'),accounts=tuple(local.get('accounts',['A1'])),
            codex_executable=os.getenv('CODEX_EXECUTABLE','codex'),
            call_timeout=90 if provider=='codex' else 30,
            turn_timeout=300 if provider=='codex' else 90,
            handoff_history_days=int(os.getenv('HANDOFF_HISTORY_DAYS','30')),
            handoff_history_limit=int(os.getenv('HANDOFF_HISTORY_LIMIT','3')),
        )
