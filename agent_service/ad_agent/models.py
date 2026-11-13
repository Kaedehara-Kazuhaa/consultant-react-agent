from typing import Literal, Any
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: Literal["tool", "answer", "clarify", "unresolved"]
    decision_summary: str = Field(min_length=1, max_length=400)
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    response: str | None = Field(default=None, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action_type == "tool":
            if not self.tool_name or self.response is not None:
                raise ValueError("Tool action requires tool_name and no response")
        elif not self.response or self.tool_name is not None or self.arguments:
            raise ValueError("Terminal action requires response and no tool invocation")
        return self

class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")
    session_id: str = Field(default="default", pattern=r"^[a-zA-Z0-9_-]{1,80}$")

class Observation(BaseModel):
    status: Literal["OK", "PARTIAL", "EMPTY", "ERROR"]
    data: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    error_code: str | None = None
    retryable: bool = False
