from dataclasses import dataclass
from pathlib import Path
import hashlib
import yaml

@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    triggers: tuple[str, ...]
    tools: tuple[str, ...]
    body: str
    digest: str

@dataclass(frozen=True)
class Profile:
    name: str
    instructions: str
    skills: tuple[Skill, ...]
    allowed_tools: frozenset[str]
    digest: str

    def select(self, message: str, intent: str | None = None) -> tuple[Skill, ...]:
        chosen = tuple(s for s in self.skills if any(t.lower() in message.lower() for t in s.triggers))
        # Intent routing includes date-only continuations that have no lexical trigger.
        names = {s.name for s in chosen} | {intent}
        if intent == "material" and any(w in message for w in ["不对", "有误", "没解决", "为什么是0"]):
            names.add("diagnose")
        if intent == "feedback":
            names.add("feedback")
        chosen = tuple(s for s in self.skills if s.name in names)
        # Missing lexical match still has a bounded general clarification skill.
        return chosen or tuple(s for s in self.skills if s.name == "clarify")

def read_document(path: Path, root: Path):
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Document path escapes deployed profile")
    if path.stat().st_size > 64_000:
        raise ValueError("Document exceeds 64 KB")
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---\n"):
        raise ValueError(f"Missing YAML front matter: {path.name}")
    _, header, body = text.split("---", 2)
    meta = yaml.safe_load(header)
    if not isinstance(meta, dict):
        raise ValueError("Document metadata must be a mapping")
    return meta, body.strip(), hashlib.sha256(text.encode()).hexdigest()

def strings(meta, field):
    value = meta.get(field, [])
    if not isinstance(value, list) or not all(isinstance(s, str) and s for s in value):
        raise ValueError(f"{field} must be a list of nonempty strings")
    return tuple(value)

def load_profile(root: Path, registered_tools: set[str]) -> Profile:
    meta, body, digest = read_document(root / "AGENTS.md", root)
    allowed = frozenset(strings(meta, "tools"))
    if not allowed <= registered_tools:
        raise ValueError("Agent document names unregistered tools")
    enabled = strings(meta, "skills")
    if len(enabled) != len(set(enabled)):
        raise ValueError("Duplicate skill names")
    skills = []
    for name in enabled:
        if not name.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Invalid skill directory name")
        sm, sb, sh = read_document(root / "skills" / name / "SKILL.md", root)
        tools = strings(sm, "tools")
        if sm.get("name") != name or not set(tools) <= allowed:
            raise ValueError("Skill name mismatch or attempted tool expansion")
        skills.append(Skill(name, str(sm.get("description", "")), strings(sm, "triggers"), tools, sb, sh))
    if not skills or len(skills) > 20:
        raise ValueError("Configure between 1 and 20 skills")
    digest = hashlib.sha256((digest + "".join(s.digest for s in skills)).encode()).hexdigest()
    return Profile(str(meta.get("name", "customer-service")), body, tuple(skills), allowed, digest)
