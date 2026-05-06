from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated

from pydantic import BaseModel, field_validator


def parse_cooldown(value: object) -> timedelta:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(seconds=int(value))
    if isinstance(value, str):
        m = re.fullmatch(r"(\d+)(s|m|h|d)", value.strip())
        if m:
            amount = int(m.group(1))
            unit = m.group(2)
            match unit:
                case "s":
                    return timedelta(seconds=amount)
                case "m":
                    return timedelta(minutes=amount)
                case "h":
                    return timedelta(hours=amount)
                case "d":
                    return timedelta(days=amount)
    raise ValueError(f"Cannot parse cooldown: {value!r}")


class StepCondition(BaseModel):
    type: str
    value: object = None
    resource: str | None = None
    from_: str | None = None
    to: str | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}


class StepParams(BaseModel):
    troop_type: str | None = None
    quantity: str | None = None
    resources: list[str] = []
    march_slots: str | None = None

    model_config = {"extra": "allow"}


class ScenarioStep(BaseModel):
    id: str
    task: str
    priority: int = 500
    cooldown: timedelta
    required: bool = False
    cooperative: bool = False
    max_attempts: int = 3
    conditions: list[StepCondition] = []
    params: StepParams = StepParams()

    @field_validator("cooldown", mode="before")
    @classmethod
    def _parse_cooldown(cls, v: object) -> timedelta:
        return parse_cooldown(v)


class Scenario(BaseModel):
    id: str
    name: str
    priority: int = 100
    repeat: bool = True
    enabled: bool = False
    conditions: list[StepCondition] = []
    steps: list[ScenarioStep] = []
