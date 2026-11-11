from dataclasses import dataclass
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    profile_dir: Path = ROOT / "profiles/default"
    trace_db: Path = ROOT / "var/runtime.sqlite"
    provider: str = "demo"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    api_token: str = ""
    business_db: str = ""
    max_actions: int = 6
    call_timeout: float = 30
    turn_timeout: float = 90
    retries: int = 1

    def __post_init__(self):
        if not 1 <= self.max_actions <= 20 or not 0 <= self.retries <= 1:
            raise ValueError("Invalid execution budget")
        if not 0 < self.call_timeout <= self.turn_timeout:
            raise ValueError("Invalid timeout budget")

    @classmethod
    def from_env(cls):
        return cls(
            profile_dir=Path(os.getenv("AGENT_PROFILE_DIR", str(ROOT / "profiles/default"))),
            trace_db=Path(os.getenv("AGENT_TRACE_DB", str(ROOT / "var/runtime.sqlite"))),
            provider=os.getenv("AGENT_PROVIDER", "demo"),
            base_url=os.getenv("MODEL_BASE_URL", ""), model=os.getenv("MODEL_NAME", ""),
            api_key=os.getenv("MODEL_API_KEY", ""), api_token=os.getenv("AGENT_API_TOKEN", ""),
            business_db=os.getenv("AGENT_BUSINESS_DB", ""),
        )
