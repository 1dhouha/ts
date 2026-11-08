from datetime import datetime
from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field, StringConstraints


ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"      # no spaces, slashes or control characters: safe in URLs, logs and Twin calls


class Observation(BaseModel):
    observation_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(pattern=ID_PATTERN)
    sensor_type: str = Field(min_length=1, max_length=64)
    zone: str = Field(default="default", min_length=1, max_length=64)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(default="", max_length=16)
    timestamp: datetime
    seq: Optional[int] = None
    tick: Optional[int] = None                       # simulation tick, when the partner sends one (used for ordering and time gaps)
    backup_ids: list[Annotated[str, StringConstraints(pattern=ID_PATTERN)]] = Field(default_factory=list, max_length=4)


class Verdict(BaseModel):
    verdict_id: str
    observation_id: str
    device_id: str
    verdict: Literal["TRUSTED", "UNTRUSTED"]
    trust_score: float
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    reason_codes: list[str]
    recommended_action: Literal["NONE", "MONITOR", "ISOLATE", "QUARANTINE"]
    issued_at: datetime
