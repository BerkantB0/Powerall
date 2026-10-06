"""Integration contract. Future sources implement these methods, not chart logic."""
from dataclasses import dataclass
from typing import Protocol

class ProviderError(Exception):
    """Safe, user-facing failure; never includes provider response bodies."""

@dataclass
class Meter:
    entity: str
    resource: str
    cost_resource: str | None
    name: str
    unit: str = 'kWh'

class Provider(Protocol):
    name: str
    def discover(self) -> list[Meter]: ...
    def readings(self, meter: Meter, start: int, end: int) -> list[tuple[int, str]]: ...
    def tariffs(self, meter: Meter) -> tuple[list[dict], list[str]]: ...
