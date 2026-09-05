"""Static site builder: renders reports into a plain HTML site for GitHub Pages."""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

log = logging.getLogger("int-monitor")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def render_markdown(md_text: str) -> str:
    return markdown.markdown(md_text, extensions=["tables", "fenced_code"])


def load_report_metas(reports_dir: Path) -> list[dict]:
    """Read all *.meta.json sidecars (newest first) and attach their .md paths."""
    metas = []
    for meta_path in Path(reports_dir).glob("*.meta.json"):
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Skipping unreadable meta %s: %s", meta_path.name, exc)
            continue
        date = str(data.get("date") or "").strip()
        md_path = meta_path.with_name(meta_path.name.replace(".meta.json", ".md"))
        if not date or not md_path.exists():
            continue
        data["date"] = date
        data["md_path"] = md_path
        metas.append(data)
    metas.sort(key=lambda m: m["date"], reverse=True)
    return metas


def build_site(reports_dir: Path, out_dir: Path, config) -> Path:
    reports_dir, out_dir = Path(reports_dir), Path(out_dir)
    metas = load_report_metas(reports_dir)
    if not metas:
        raise RuntimeError(f"no reports found in {reports_dir.resolve()} - nothing to publish")

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    base_ctx = {
        "site_title": config.site_title,
        "site_description": config.site_description,
        "base": config.base_url,
    }

    out_reports = out_dir / "reports"
    out_reports.mkdir(parents=True, exist_ok=True)

    report_tpl = env.get_template("report.html")
    for meta in metas:
        md_text = meta["md_path"].read_text(encoding="utf-8")
        html = report_tpl.render(
            **base_ctx,
            date=meta["date"],
            headline=meta.get("headline", ""),
            sentiment=meta.get("sentiment"),
            report_html=render_markdown(md_text),
        )
        (out_reports / f"{meta['date']}.html").write_text(html, encoding="utf-8")

    latest, older = metas[0], metas[1:]
    latest_html = render_markdown(latest["md_path"].read_text(encoding="utf-8"))
    index_html = env.get_template("index.html").render(
        **base_ctx,
        latest=latest,
        report_html=latest_html,
        older=older,
    )
    (out_dir / "index.html").write_text(index_html, encoding="utf-8")

    notfound_tpl = env.get_template("404.html")
    (out_dir / "404.html").write_text(notfound_tpl.render(**base_ctx), encoding="utf-8")

    static_src = TEMPLATES_DIR / "static"
    if static_src.exists():
        shutil.copytree(static_src, out_dir / "static", dirs_exist_ok=True)

    log.info("Site built: %d report page(s) -> %s", len(metas), out_dir.resolve())
    return out_dir
