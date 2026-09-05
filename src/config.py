"""Configuration loading for the Intrum AB daily monitor."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    company: str
    report_language: str
    topic_terms: list[str]
    rss_sources: list[dict]
    lookback_hours: int
    first_run_days: int
    max_new_items: int
    social_topic: str
    social_days: int
    social_search: str
    subreddits: str
    evidence_max_chars: int
    model: str
    temperature: float
    max_tokens: int
    api_key_env: str
    last30days_repo: str
    last30days_ref: str
    site_title: str
    site_description: str
    base_url: str


def load_config(path: str | Path = "config.yml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path.resolve()}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    llm = raw.get("llm") or {}
    social = raw.get("social") or {}
    l30 = raw.get("last30days") or {}
    site = raw.get("site") or {}

    company = str(raw.get("company") or "").strip()
    topic_terms = [str(t).strip() for t in (raw.get("topic_terms") or []) if str(t).strip()]
    rss_sources = list(raw.get("rss_sources") or [])
    if not company or not topic_terms or not rss_sources:
        raise ValueError(
            "config.yml must define non-empty 'company', 'topic_terms' and 'rss_sources'"
        )

    base_url = str(site.get("base_url") or "").strip()
    base_url = base_url.rstrip("/") + "/" if base_url else ""

    return Config(
        company=company,
        report_language=str(raw.get("report_language") or "en"),
        topic_terms=topic_terms,
        rss_sources=rss_sources,
        lookback_hours=int(raw.get("lookback_hours") or 48),
        first_run_days=int(raw.get("first_run_days") or 7),
        max_new_items=int(raw.get("max_new_items") or 40),
        social_topic=str(social.get("topic") or company),
        social_days=int(social.get("days") or 30),
        social_search=str(social.get("search") or ""),
        subreddits=str(social.get("subreddits") or ""),
        evidence_max_chars=int(social.get("evidence_max_chars") or 35000),
        model=str(llm.get("model") or "google/gemini-2.5-flash"),
        temperature=float(llm.get("temperature") if llm.get("temperature") is not None else 0.2),
        max_tokens=int(llm.get("max_tokens") or 6000),
        api_key_env=str(llm.get("api_key_env") or "OPENROUTER_API_KEY"),
        last30days_repo=str(l30.get("repo") or "https://github.com/mvanhorn/last30days-skill"),
        last30days_ref=str(l30.get("ref") or "main"),
        site_title=str(site.get("title") or f"{company} Daily Monitor"),
        site_description=str(site.get("description") or ""),
        base_url=base_url,
    )
