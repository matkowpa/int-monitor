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
    """Read all *.meta.json sidecars (newest first) and attach their .md paths.

    The filename stem is the run id: the first run of a day is `YYYY-MM-DD`,
    later runs that day get `YYYY-MM-DD-HHMM` (UTC; plus `-N` on minute
    collisions), so repeated runs never overwrite each other and the plain
    string sort is chronological.
    """
    metas = []
    for meta_path in Path(reports_dir).glob("*.meta.json"):
        run_id = meta_path.name[: -len(".meta.json")]
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Skipping unreadable meta %s: %s", meta_path.name, exc)
            continue
        date = str(data.get("date") or "").strip() or run_id[:10]
        md_path = meta_path.with_name(run_id + ".md")
        if not md_path.exists():
            continue
        data["date"] = date
        data["run_id"] = run_id
        if run_id == date:
            data["label"] = date
        elif len(run_id) >= 15 and run_id[10] == "-":
            data["label"] = f"{date} · {run_id[11:13]}:{run_id[13:15]} UTC"
        else:
            data["label"] = run_id
        data["md_path"] = md_path
        metas.append(data)
    # Sort by actual run time (generated_at, UTC), not run_id: run ids are
    # date-based and can cross the local/UTC midnight boundary (e.g.
    # `2026-09-06` created before `2026-09-05-2305`), so the string order of
    # run ids is not always chronological.
    metas.sort(key=lambda m: (str(m.get("generated_at") or ""), m["run_id"]),
               reverse=True)
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
            run_id=meta["run_id"],
            label=meta["label"],
            report_html=render_markdown(md_text),
        )
        (out_reports / f"{meta['run_id']}.html").write_text(html, encoding="utf-8")

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

    # Skip Jekyll processing on the Pages side entirely: the site is plain
    # pre-built HTML, and a stray Jekyll build (e.g. after a Pages misconfig)
    # would only add failure modes.
    (out_dir / ".nojekyll").touch()

    log.info("Site built: %d report page(s) -> %s", len(metas), out_dir.resolve())
    return out_dir
