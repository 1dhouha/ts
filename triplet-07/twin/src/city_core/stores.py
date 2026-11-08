"""Serialized holders; the public holder has no path to private world data."""
from dataclasses import dataclass
from threading import RLock
from typing import Any


@dataclass(frozen=True, slots=True)
class PublicView:
    metadata: Any
    topology: Any


@dataclass(slots=True)
class PublicStore:
    lock: Any
    current: PublicView | None = None
    token_count: int = 0


@dataclass(slots=True)
class PrivateStore:
    lock: Any
    current: Any = None


def store_pair():
    lock = RLock()
    return PrivateStore(lock), PublicStore(lock)
