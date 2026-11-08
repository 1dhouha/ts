"""Protocol adapters: HOW data arrives. Each one yields raw payloads; nothing here knows the data format.
(HTTP push is the API's /ingest route. MCP and MQTT adapters plug in here later.)"""
import csv
import json
from typing import Any, Iterator, Optional, Protocol

import httpx


class ProtocolAdapter(Protocol):
    def fetch(self) -> Iterator[Any]: ...


class CsvReplayAdapter:
    """Replays a recorded file. CSV is for tests and replays only, never the live contract."""

    def __init__(self, path: str):
        self.path = path

    def fetch(self):
        with open(self.path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                yield dict(row)


class HttpPollAdapter:
    """Pulls from a partner endpoint that returns one reading or a JSON array of readings."""

    def __init__(self, url: str, token: Optional[str] = None, timeout: float = 2.0,
                 client: Optional[httpx.Client] = None):
        self.url, self.token, self.timeout = url, token, timeout
        self.client = client or httpx.Client()

    def fetch(self):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        r = self.client.get(self.url, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        try:
            body = r.json()
        except ValueError:
            yield r.text                       # not JSON (for example XML): the format adapter decides
            return
        for item in (body if isinstance(body, list) else [body]):
            yield item
