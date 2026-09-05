"""Data models for the Intrum AB daily monitor pipeline."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


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


def make_id(*parts: str) -> str:
    raw = "|".join(p or "" for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


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
class SocialItem:
    id: str
    platform: str
    title: str
    url: str
    snippet: str = ""
    engagement: str = ""
    posted: datetime | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["posted"] = iso(self.posted)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "SocialItem":
        title = str(data.get("title") or "")
        return cls(
            id=str(data.get("id") or make_id(str(data.get("platform") or ""), title.lower())),
            platform=str(data.get("platform") or "web"),
            title=title,
            url=str(data.get("url") or ""),
            snippet=str(data.get("snippet") or ""),
            engagement=str(data.get("engagement") or ""),
            posted=parse_iso(data.get("posted")),
        )


@dataclass
class Story:
    id: str
    title: str
    status: str = "active"  # active | updated | resolved
    summary: str = ""
    first_seen: str = ""
    last_seen: str = ""
    urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Story":
        status = str(data.get("status") or "active").lower()
        if status not in ("active", "updated", "resolved"):
            status = "active"
        urls = [str(u) for u in (data.get("urls") or []) if str(u).strip()][:5]
        return cls(
            id=str(data.get("id") or "").strip(),
            title=str(data.get("title") or ""),
            status=status,
            summary=str(data.get("summary") or ""),
            first_seen=str(data.get("first_seen") or ""),
            last_seen=str(data.get("last_seen") or ""),
            urls=urls,
        )


@dataclass
class Threat:
    title: str
    severity: str = "low"  # high | medium | low
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Sentiment:
    score: float = 0.0  # -1.0 .. 1.0
    label: str = "neutral"
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AnalysisResult:
    headline: str
    report_md: str
    highlights: list[str] = field(default_factory=list)
    sentiment: Sentiment = field(default_factory=Sentiment)
    threats: list[Threat] = field(default_factory=list)
    stories: list[Story] = field(default_factory=list)


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
