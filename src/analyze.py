"""LLM analysis: OpenRouter call -> strict-JSON daily report with stories update."""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from .models import AnalysisResult, NewsItem, Sentiment, SocialItem, Story, Threat

log = logging.getLogger("int-monitor")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class AnalysisError(Exception):
    pass


def _serialize_items(new_items: list[NewsItem], social_items: list[SocialItem]) -> dict:
    return {
        "new_news_items": [
            {
                "title": it.title,
                "source": it.source,
                "url": it.url,
                "published": it.published.strftime("%Y-%m-%d %H:%M UTC") if it.published else None,
                "snippet": it.snippet[:300],
            }
            for it in new_items
        ],
        "social_posts": [
            {
                "platform": it.platform,
                "title": it.title,
                "url": it.url,
                "engagement": it.engagement,
                "posted": it.posted.strftime("%Y-%m-%d") if it.posted else None,
                "snippet": (it.snippet or it.title)[:300],
            }
            for it in social_items
        ],
    }


def build_messages(config, new_items: list[NewsItem], social_items: list[SocialItem],
                   stories: list[Story], prev_headline: str | None) -> list[dict]:
    today = date.today().isoformat()
    system = f"""You are a corporate intelligence analyst producing a DAILY monitoring report on {config.company} for internal stakeholders. Today is {today}.

RULES:
- Use ONLY the supplied news items and social posts as factual sources. Never invent facts, numbers or events.
- Every substantive claim must reference at least one source as a markdown link to the exact supplied URL.
- Write the report in {config.report_language}.
- report_md must use exactly these H2 sections, in this order:
  ## New Today
  ## Ongoing Stories
  ## Social & Sentiment
  ## Threats & Risks
- "New Today": the most important NEW items since the last report; group related items; each entry = title as markdown link + 1-3 sentence summary.
- "Ongoing Stories": updates on the previously tracked stories supplied in context. If a story has no development today, omit it. Explicitly mark stories that are now resolved.
- "Social & Sentiment": what social/alternative sources say; notable posts with links; overall tone. If no social data was supplied, say screening found nothing notable.
- "Threats & Risks": potential threats to the company (reputational, legal, financial, operational) derived ONLY from the supplied material; each with a severity (high/medium/low).
- Concise: the whole report must read in under 3 minutes.

OUTPUT: return ONLY one JSON object, no markdown fences, with keys:
- "headline": string, max 120 chars, today's single-sentence takeaway
- "report_md": string, the full markdown report per the section rules
- "highlights": array of 3-6 short strings (the day's key takeaways)
- "sentiment": {{"score": number between -1 and 1, "label": "positive"|"neutral"|"negative", "rationale": string}}
- "threats": array of {{"title": string, "severity": "high"|"medium"|"low", "summary": string}}
- "stories": array of {{"id": string, "title": string, "status": "active"|"updated"|"resolved", "summary": string, "first_seen": "YYYY-MM-DD", "last_seen": "YYYY-MM-DD", "urls": array of up to 5 URLs}}

STORIES RULES: the stories array is the FULL updated story list. Keep the ids of CURRENT STORIES (may add new ones with new kebab-case ids). status: "active" (no change), "updated" (new development today), "resolved". Set last_seen to today for new/updated stories. Keep at most 20 stories, most important first."""
    user = {
        "previous_headline": prev_headline,
        "current_stories": [s.to_dict() for s in stories],
        **_serialize_items(new_items, social_items),
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]

def _debug_dump(content: str, attempt: int) -> None:
    """Persist raw LLM responses under .last30days-run/ for diagnosis."""
    try:
        debug_dir = Path(".last30days-run")
        debug_dir.mkdir(exist_ok=True)
        (debug_dir / f"llm-response-{attempt}.txt").write_text(content or "", encoding="utf-8")
    except OSError:
        pass


def call_openrouter(model: str, messages: list[dict], api_key: str,
                    temperature: float = 0.2, max_tokens: int = 6000,
                    timeout: int = 120, response_format: dict | None = None) -> str:
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        body["response_format"] = response_format
    req = urllib.request.Request(
        OPENROUTER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/int-monitor",
            "X-Title": "int-monitor",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except OSError:
            pass
        raise AnalysisError(f"OpenRouter HTTP {exc.code}: {detail}") from exc
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AnalysisError(f"unexpected OpenRouter response shape: {data}") from exc


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise AnalysisError("no JSON object found in model output")
    candidate = text[start:end + 1]
    variants = [
        candidate,
        re.sub(r",\s*([}\]])", r"\1", candidate),  # trailing commas
    ]
    last_exc: ValueError | None = None
    for variant in variants:
        try:
            # strict=False tolerates raw control characters inside strings
            return json.loads(variant, strict=False)
        except ValueError as exc:
            last_exc = exc
    raise AnalysisError(f"invalid JSON from model: {last_exc}") from last_exc


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:80] or "story"


def parse_analysis(text: str) -> AnalysisResult:
    data = _extract_json(text)
    headline = str(data.get("headline") or "").strip()
    report_md = str(data.get("report_md") or "").strip()
    if not headline or not report_md:
        raise AnalysisError("model output missing 'headline' or 'report_md'")

    highlights = [str(h).strip() for h in (data.get("highlights") or []) if str(h).strip()][:6]

    sent = data.get("sentiment") or {}
    if not isinstance(sent, dict):
        sent = {}
    try:
        score = max(-1.0, min(1.0, float(sent.get("score", 0))))
    except (TypeError, ValueError):
        score = 0.0
    sentiment = Sentiment(
        score=score,
        label=str(sent.get("label") or "neutral").lower(),
        rationale=str(sent.get("rationale") or ""),
    )

    threats = []
    for t in (data.get("threats") or []):
        if isinstance(t, dict) and str(t.get("title") or "").strip():
            severity = str(t.get("severity") or "low").lower()
            threats.append(Threat(
                title=str(t["title"]).strip()[:200],
                severity=severity if severity in ("high", "medium", "low") else "low",
                summary=str(t.get("summary") or ""),
            ))

    stories, seen_ids = [], set()
    for s in (data.get("stories") or []):
        story = Story.from_dict(s) if isinstance(s, dict) else None
        if story and story.id and story.title and story.id not in seen_ids:
            story.id = _slugify(story.id)
            seen_ids.add(story.id)
            stories.append(story)

    return AnalysisResult(
        headline=headline[:200],
        report_md=report_md,
        highlights=highlights,
        sentiment=sentiment,
        threats=threats,
        stories=stories,
    )

def build_fallback_report(config, new_items: list[NewsItem], social_items: list[SocialItem],
                          stories: list[Story], reason: str) -> AnalysisResult:
    """Templated report used when the LLM is unavailable or returns invalid output."""
    today = date.today().isoformat()
    lines = ["## New Today", ""]
    if new_items:
        for it in new_items[:20]:
            pub = it.published.strftime("%Y-%m-%d %H:%M UTC") if it.published else "date unknown"
            lines.append(f"- [{it.title}]({it.url}) — *{it.source}*, {pub}")
    else:
        lines.append("_No new items found in the monitored feeds._")
    lines += ["", "## Ongoing Stories", ""]
    if stories:
        for s in stories:
            lines.append(f"- **{s.title}** ({s.status}) — {s.summary}")
    else:
        lines.append("_No stories tracked yet._")
    lines += ["", "## Social & Sentiment", ""]
    if social_items:
        for it in social_items[:10]:
            lines.append(f"- [{it.title}]({it.url}) — *{it.platform}*"
                         + (f", {it.engagement}" if it.engagement else ""))
    else:
        lines.append(f"_Social screening unavailable in this run ({reason})._")
    lines += ["", "## Threats & Risks", "",
              "_Automated threat analysis unavailable in this run; review the raw items above._"]
    return AnalysisResult(
        headline=f"{config.company} daily digest — {today}",
        report_md="\n".join(lines),
        highlights=[],
        sentiment=Sentiment(),
        threats=[],
        stories=stories,
    )


def analyze(config, new_items: list[NewsItem], social_items: list[SocialItem],
            stories: list[Story], prev_headline: str | None) -> AnalysisResult:
    api_key = os.environ.get(config.api_key_env, "")
    if not api_key:
        log.warning("%s not set - publishing fallback report", config.api_key_env)
        return build_fallback_report(config, new_items, social_items, stories, "no API key")

    base_messages = build_messages(config, new_items, social_items, stories, prev_headline)
    last_err, messages = None, base_messages
    for attempt in (1, 2):
        # Attempt 1 uses provider JSON mode (guaranteed-valid JSON on supporting
        # models, e.g. Gemini); attempt 2 falls back to plain prompting.
        response_format = {"type": "json_object"} if attempt == 1 else None
        content = None
        try:
            content = call_openrouter(config.model, messages, api_key,
                                      config.temperature, config.max_tokens,
                                      response_format=response_format)
            _debug_dump(content, attempt)
            return parse_analysis(content)
        except (AnalysisError, OSError, ValueError) as exc:
            last_err = exc
            log.warning("LLM attempt %d failed: %s", attempt, exc)
            if isinstance(exc, AnalysisError) and content:
                messages = base_messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"Your previous answer was invalid: {exc}. "
                     "Return ONLY the corrected single JSON object."},
                ]
    log.warning("LLM failed after retries - publishing fallback report")
    return build_fallback_report(config, new_items, social_items, stories, str(last_err))


def prune_stories(stories: list[Story], today: str, cap: int = 20) -> list[Story]:
    """Drop long-resolved stories and cap the list."""
    try:
        today_dt = date.fromisoformat(today)
    except ValueError:
        return stories[:cap]

    def is_stale(story: Story) -> bool:
        if story.status != "resolved" or not story.last_seen:
            return False
        try:
            return (today_dt - date.fromisoformat(story.last_seen)).days > 14
        except ValueError:
            return False

    kept = [s for s in stories if not is_stale(s)]
    return kept[:cap]


