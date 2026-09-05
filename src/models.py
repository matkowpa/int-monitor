"""Data models for the Intrum AB daily monitor pipeline."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def make_id(*parts: str) -> str:
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


@dataclass
class NewsItem:
    id: str
    title: str
    url: str
    source: str
    published: datetime | None = None
    snippet: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = iso(self.published)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "NewsItem":
        title = str(data.get("title") or "")
        return cls(
            id=str(data.get("id") or make_id(str(data.get("source") or ""), title.lower())),
            title=title,
            url=str(data.get("url") or ""),
            source=str(data.get("source") or ""),
            published=parse_iso(data.get("published")),
            snippet=str(data.get("snippet") or ""),
        )


@dataclass
class State:
    last_run: str | None = None
    seen_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "State":
        return cls(
            last_run=data.get("last_run"),
            seen_ids=[str(s) for s in (data.get("seen_ids") or [])],
        )
