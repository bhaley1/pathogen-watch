"""Static site generator for Pathogen Watch.

Consumes the cluster/event data produced by the core pipeline and writes:
  site/index.html              dashboard
  site/timeline.html           rolling alert timeline
  site/methods.html            methods/caveats
  site/data.html               data downloads + API docs
  site/about.html              about/colophon
  site/clusters/<slug>.html    one page per human-containing cluster
  site/data/clusters.json      machine-readable cluster export
  site/data/clusters.csv       spreadsheet-friendly cluster export
  site/data/events.json/csv    alert events
  site/data/near_neighbors.json/csv
  site/feed.xml                RSS 2.0 feed
"""

from __future__ import annotations

import csv
import json
import logging
import re
import shutil
from dataclasses import asdict
from datetime import date, datetime, timedelta
from email.utils import format_datetime as rfc2822
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config as core_config
from ..cluster import Cluster, CrossClusterNearNeighbor
from ..parser import Isolate
from ..state import Event, EventType
from . import config as site_config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(s: str) -> str:
    """URL-safe slug. Used for cluster page filenames."""
    return _SLUG_RE.sub("-", s.lower()).strip("-")


def cluster_slug(pathogen: str, pds_acc: str) -> str:
    return f"{slugify(pathogen)}-{slugify(pds_acc)}"


def fmt_long(d: date) -> str:
    return d.strftime("%A, %B %-d, %Y") if hasattr(d, "strftime") else str(d)


def fmt_short(d) -> str:
    if d is None:
        return "—"
    if isinstance(d, str):
        return d
    return d.strftime("%Y-%m-%d")


_EVENT_BADGE = {
    EventType.NEW_HUMAN_CLUSTER: ("badge-event-new", "New cluster"),
    EventType.NEW_HUMAN_IN_CLUSTER: ("badge-event-grow", "Human added"),
    EventType.NEW_NONHUMAN_NEAR_HUMAN: ("badge-event-mixed", "Nonhuman added"),
}


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

def _flatten_events(
    events_by_pathogen: dict[str, list[Event]],
    run_date: date,
) -> list[dict[str, Any]]:
    """Turn pipeline Event objects into template-friendly dicts."""
    out: list[dict[str, Any]] = []
    for pathogen, events in events_by_pathogen.items():
        for ev in events:
            badge_class, label = _EVENT_BADGE[ev.event_type]
            out.append({
                "pathogen": pathogen,
                "event_type": ev.event_type.value,
                "pds_acc": ev.pds_acc,
                "cluster_slug": cluster_slug(pathogen, ev.pds_acc),
                "summary": ev.summary,
                "new_pdt_accs": ev.new_pdt_accs or [],
                "badge_class": badge_class,
                "label": label,
                "detected_iso": run_date.isoformat(),
                "detected_short": run_date.strftime("%b %-d"),
                "detected_date": run_date,
            })
    return out


def _enrich_event_with_cluster(
    ev: dict[str, Any],
    cluster_lookup: dict[tuple[str, str], Cluster],
) -> None:
    """Add counts and search text from the cluster the event refers to."""
    c = cluster_lookup.get((ev["pathogen"], ev["pds_acc"]))
    ev["n_human"] = c.n_human if c else 0
    ev["n_nonhuman"] = c.n_nonhuman if c else 0
    ev["search_text"] = " ".join([
        ev["pds_acc"], ev["pathogen"], ev["label"], ev["summary"],
        " ".join(ev["new_pdt_accs"]),
        " ".join(c.countries) if c else "",
        " ".join(c.nonhuman_sources) if c else "",
    ]).lower()


def _prepare_mixed_clusters(
    mixed_clusters_by_pathogen: dict[str, list[Cluster]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pathogen, clusters in mixed_clusters_by_pathogen.items():
        for c in clusters:
            sources_list = sorted(c.nonhuman_sources)
            countries_list = sorted(c.countries)
            tg = c.tightest_human_min_same
            out.append({
                "pathogen": pathogen,
                "pds_acc": c.pds_acc,
                "slug": cluster_slug(pathogen, c.pds_acc),
                "n_human": c.n_human,
                "n_nonhuman": c.n_nonhuman,
                "tightest_gap": tg,
                "tightest_gap_sort": tg if tg is not None else 99999,
                "latest_iso": c.latest_collection.isoformat() if c.latest_collection else "",
                "latest_short": _best_date_string(c),
                "sources": ", ".join(sources_list[:3]) + (
                    f" (+{len(sources_list)-3})" if len(sources_list) > 3 else ""
                ) if sources_list else "—",
                "sources_full": ", ".join(sources_list) if sources_list else "",
                "countries": ", ".join(countries_list[:3]) + (
                    f" (+{len(countries_list)-3})" if len(countries_list) > 3 else ""
                ) if countries_list else "—",
                "search_text": " ".join([
                    c.pds_acc, pathogen,
                    " ".join(sources_list), " ".join(countries_list),
                ]).lower(),
            })
    out.sort(key=lambda r: (r["latest_iso"] or ""), reverse=True)
    return out


def _prepare_neighbors(
    neighbors_by_pathogen: dict[str, dict[int, list[CrossClusterNearNeighbor]]],
    threshold: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pathogen, by_thresh in neighbors_by_pathogen.items():
        seen_pdts: set[str] = set()
        for n in by_thresh.get(threshold, []):
            iso = n.isolate
            if iso.pdt_acc in seen_pdts:
                continue
            seen_pdts.add(iso.pdt_acc)
            source = iso.isolation_source or iso.host or "—"
            out.append({
                "pathogen": pathogen,
                "pdt_acc": iso.pdt_acc,
                "pds_acc": iso.pds_acc or "—",
                "cluster_slug": cluster_slug(pathogen, iso.pds_acc) if iso.pds_acc else "",
                "min_diff": iso.min_diff,
                "source": source,
                "geo": iso.geo_loc_name or "—",
                "collection_iso": iso.collection_date.isoformat() if iso.collection_date else "",
                "collection_short": iso.collection_date.isoformat() if iso.collection_date else "—",
                "search_text": " ".join([
                    iso.pdt_acc, iso.pds_acc or "", source, iso.geo_loc_name or "",
                ]).lower(),
            })
    out.sort(key=lambda r: r["min_diff"] if r["min_diff"] is not None else 99999)
    return out


def _prepare_cluster_detail(
    pathogen: str,
    c: Cluster,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Per-cluster template context."""
    dates = [i.collection_date for i in c.isolates if i.collection_date]
    if dates:
        d_min, d_max = min(dates), max(dates)
        if d_min == d_max:
            span = d_min.isoformat()
        else:
            span = f"{d_min.isoformat()} → {d_max.isoformat()}"
    else:
        span = "unknown dates"

    def iso_dict(iso: Isolate) -> dict[str, Any]:
        src = iso.isolation_source or iso.host or "—"
        return {
            "pdt_acc": iso.pdt_acc,
            "min_same": iso.min_same,
            "source": (src[:60] + "…") if len(src) > 60 else src,
            "source_full": src,
            "geo": iso.geo_loc_name,
            "collection_short": iso.collection_date.isoformat() if iso.collection_date else "—",
            "serovar": iso.serovar,
            "biosample_acc": iso.biosample_acc,
        }

    return {
        "pathogen": pathogen,
        "pds_acc": c.pds_acc,
        "slug": cluster_slug(pathogen, c.pds_acc),
        "n_total": c.n_total,
        "n_human": c.n_human,
        "n_nonhuman": c.n_nonhuman,
        "tightest_gap": c.tightest_human_min_same,
        "country_count": len(c.countries),
        "date_span": span,
        "ncbi_url": c.url(),
        "humans": [iso_dict(i) for i in c.humans],
        "nonhumans": [iso_dict(i) for i in c.nonhumans],
        "history": history or [],
    }


# ---------------------------------------------------------------------------
# History log — append-only per-run JSONL
# ---------------------------------------------------------------------------

HISTORY_PATH = core_config.STATE_DIR / "history.jsonl"


def append_history(events: list[dict[str, Any]], run_date: date) -> None:
    """Append today's events to an append-only JSONL file for timeline/RSS."""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps({
                "detected_date": run_date.isoformat(),
                "pathogen": ev["pathogen"],
                "event_type": ev["event_type"],
                "pds_acc": ev["pds_acc"],
                "summary": ev["summary"],
                "new_pdt_accs": ev["new_pdt_accs"],
                "n_human": ev.get("n_human", 0),
                "n_nonhuman": ev.get("n_nonhuman", 0),
            }) + "\n")


def load_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ---------------------------------------------------------------------------
# Data exports
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            # Coerce lists to "|"-joined strings for CSV friendliness
            out = {k: ("|".join(v) if isinstance(v, list) else v) for k, v in row.items() if k in fieldnames}
            w.writerow(out)


def write_data_exports(
    site_dir: Path,
    mixed_clusters: list[dict[str, Any]],
    flat_events: list[dict[str, Any]],
    near_neighbors_all: dict[int, list[dict[str, Any]]],
    run_date: date,
) -> None:
    data_dir = site_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Clusters — enriched for export
    clusters_export = [{
        "pathogen": r["pathogen"],
        "pds_acc": r["pds_acc"],
        "n_human": r["n_human"],
        "n_nonhuman": r["n_nonhuman"],
        "tightest_human_min_same": r["tightest_gap"],
        "latest_collection": r["latest_iso"] or None,
        "countries": r["countries"],
        "nonhuman_sources": r["sources_full"],
        "ncbi_url": f"https://www.ncbi.nlm.nih.gov/pathogens/isolates/#{r['pds_acc']}",
        "site_url": f"{site_config.SITE_BASE_URL}/clusters/{r['slug']}.html",
    } for r in mixed_clusters]

    _write_json(data_dir / "clusters.json", {
        "generated_at": run_date.isoformat(),
        "count": len(clusters_export),
        "clusters": clusters_export,
    })
    _write_csv(data_dir / "clusters.csv", clusters_export,
               fieldnames=["pathogen", "pds_acc", "n_human", "n_nonhuman",
                           "tightest_human_min_same", "latest_collection",
                           "countries", "nonhuman_sources", "ncbi_url", "site_url"])

    # Events — today's
    events_export = [{
        "detected_date": run_date.isoformat(),
        "pathogen": ev["pathogen"],
        "event_type": ev["event_type"],
        "pds_acc": ev["pds_acc"],
        "summary": ev["summary"],
        "new_pdt_accs": ev["new_pdt_accs"],
        "n_human": ev.get("n_human", 0),
        "n_nonhuman": ev.get("n_nonhuman", 0),
    } for ev in flat_events]
    _write_json(data_dir / "events.json", {
        "generated_at": run_date.isoformat(),
        "count": len(events_export),
        "events": events_export,
    })
    _write_csv(data_dir / "events.csv", events_export,
               fieldnames=["detected_date", "pathogen", "event_type", "pds_acc",
                           "summary", "new_pdt_accs", "n_human", "n_nonhuman"])

    # Near-neighbors — all thresholds, dedup by pdt+threshold
    nn_export = []
    for thresh, rows in near_neighbors_all.items():
        for r in rows:
            nn_export.append({
                "pathogen": r["pathogen"],
                "pdt_acc": r["pdt_acc"],
                "pds_acc": r["pds_acc"],
                "min_diff": r["min_diff"],
                "threshold": thresh,
                "source": r["source"],
                "geo": r["geo"],
                "collection_date": r["collection_iso"] or None,
            })
    _write_json(data_dir / "near_neighbors.json", {
        "generated_at": run_date.isoformat(),
        "count": len(nn_export),
        "near_neighbors": nn_export,
    })
    _write_csv(data_dir / "near_neighbors.csv", nn_export,
               fieldnames=["pathogen", "pdt_acc", "pds_acc", "min_diff", "threshold",
                           "source", "geo", "collection_date"])


# ---------------------------------------------------------------------------
# RSS feed
# ---------------------------------------------------------------------------

def write_rss(site_dir: Path, history_rows: list[dict[str, Any]], run_date: date) -> None:
    """RSS 2.0 feed of the most recent N alert events."""
    # Newest first
    history_rows = sorted(history_rows, key=lambda r: r["detected_date"], reverse=True)[:50]

    items_xml = []
    for r in history_rows:
        slug = cluster_slug(r["pathogen"], r["pds_acc"])
        link = f"{site_config.SITE_BASE_URL}/clusters/{slug}.html"
        try:
            pub = datetime.fromisoformat(r["detected_date"])
        except ValueError:
            pub = datetime.now()
        pub_str = rfc2822(pub)
        label = _EVENT_BADGE[EventType(r["event_type"])][1]
        title = f"[{r['pathogen']}] {label}: {r['pds_acc']}"
        desc_text = (
            f"{r['summary']}. {r['n_human']} human / {r['n_nonhuman']} nonhuman isolate(s)."
        )
        items_xml.append(f"""
    <item>
      <title><![CDATA[{title}]]></title>
      <link>{link}</link>
      <guid isPermaLink="false">{r['detected_date']}-{slug}-{r['event_type']}</guid>
      <pubDate>{pub_str}</pubDate>
      <category>{r['pathogen']}</category>
      <description><![CDATA[{desc_text}]]></description>
    </item>""")

    last_build = rfc2822(datetime.now())
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{site_config.SITE_TITLE} — daily alerts</title>
    <link>{site_config.SITE_BASE_URL}/</link>
    <atom:link href="{site_config.SITE_BASE_URL}/feed.xml" rel="self" type="application/rss+xml" />
    <description>{site_config.SITE_DESCRIPTION}</description>
    <language>en-us</language>
    <lastBuildDate>{last_build}</lastBuildDate>
{''.join(items_xml)}
  </channel>
</rss>
"""
    (site_dir / "feed.xml").write_text(rss)


# ---------------------------------------------------------------------------
# Edition number — days since project start (gives the front page a "№ N" stamp)
# ---------------------------------------------------------------------------

PROJECT_START = date(2026, 5, 1)


def edition_no(today: date) -> int:
    return max(1, (today - PROJECT_START).days + 1)


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------



def _best_date_string(c) -> str:
    """Pick the most precise date string we can honestly display for a cluster.

    Prefers the raw NCBI string of the isolate with the latest parsed date,
    so 'YYYY-MM' stays as 'YYYY-MM' instead of being normalized to 'YYYY-MM-01'.
    Falls back to the parsed ISO date if no raw string is available.
    """
    if not c.isolates:
        return "—"
    # Find the isolate whose parsed date == cluster.latest_collection
    target = c.latest_collection
    if target is None:
        return "—"
    for iso in c.isolates:
        if iso.collection_date == target:
            raw = getattr(iso, "collection_date_raw", None)
            if raw:
                return raw.strip()[:10]  # cap at YYYY-MM-DD length
            return target.isoformat()
    return target.isoformat()


def render_site(
    run_date: date,
    events_by_pathogen: dict[str, list[Event]],
    mixed_clusters_by_pathogen: dict[str, list[Cluster]],
    near_neighbors_by_pathogen: dict[str, dict[int, list[CrossClusterNearNeighbor]]],
    all_clusters_by_pathogen: dict[str, dict[str, Cluster]],
    pdg_releases: dict[str, str],
) -> None:
    """Render the full static site to SITE_DIR."""
    site_dir = site_config.SITE_DIR
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "clusters").mkdir(exist_ok=True)

    # Jinja env
    env = Environment(
        loader=FileSystemLoader(str(site_config.TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    site_ctx = {
        "title": site_config.SITE_TITLE,
        "description": site_config.SITE_DESCRIPTION,
        "author": site_config.SITE_AUTHOR,
        "affiliation": site_config.SITE_AFFILIATION,
        "base_url": site_config.SITE_BASE_URL,
    }
    common = {
        "site": site_ctx,
        "giscus": site_config.GISCUS,
        "giscus_enabled": site_config.giscus_enabled(),
        "run_date_long": run_date.strftime("%B %-d, %Y"),
        "run_date": run_date,
        "edition_no": edition_no(run_date),
        "latest_pdg": next(iter(pdg_releases.values()), "—") if pdg_releases else "—",
        "rel_root": "",
    }

    # Lookup for enriching events
    cluster_lookup: dict[tuple[str, str], Cluster] = {}
    for pathogen, cmap in all_clusters_by_pathogen.items():
        for pds, c in cmap.items():
            cluster_lookup[(pathogen, pds)] = c

    flat_events = _flatten_events(events_by_pathogen, run_date)
    for ev in flat_events:
        _enrich_event_with_cluster(ev, cluster_lookup)
    # Sort newest first
    flat_events.sort(key=lambda e: e["detected_iso"], reverse=True)

    mixed = _prepare_mixed_clusters(mixed_clusters_by_pathogen)
    tight5 = _prepare_neighbors(near_neighbors_by_pathogen, 5)
    tight_all_thresh = {
        t: _prepare_neighbors(near_neighbors_by_pathogen, t)
        for t in core_config.SNP_THRESHOLDS
    }

    # Totals for tiles
    n_human_clusters = sum(
        1 for cmap in all_clusters_by_pathogen.values()
        for c in cmap.values()
        if c.n_human >= 1
    )
    totals = {
        "new_events": len(flat_events),
        "pathogens": len(core_config.PATHOGENS),
        "mixed_clusters": len(mixed),
        "human_clusters": n_human_clusters,
        "tight_neighbors": len(tight5),
    }
    pathogen_counts: dict[str, int] = {p: 0 for p in core_config.PATHOGENS}
    for ev in flat_events:
        pathogen_counts[ev["pathogen"]] = pathogen_counts.get(ev["pathogen"], 0) + 1

    # ----- index.html -----
    rendered = env.get_template("dashboard.html").render(
        **common,
        page_url="/",
        totals=totals,
        pathogen_counts=pathogen_counts,
        recent_events=flat_events,
        recent_days=site_config.FRONT_PAGE_RECENT_DAYS,
        window_days=core_config.RECENT_WINDOW_DAYS,
        mixed_clusters=mixed,
        tight_neighbors=tight5,
    )
    (site_dir / "index.html").write_text(rendered)
    log.info("Wrote index.html")

    # ----- timeline.html -----
    # Append today's events to history, then load and group
    append_history(flat_events, run_date)
    history = load_history()
    # Group by date desc
    timeline_days: dict[str, list[dict[str, Any]]] = {}
    for h in sorted(history, key=lambda r: r["detected_date"], reverse=True):
        timeline_days.setdefault(h["detected_date"], []).append(h)
    days_for_template = []
    for d_iso, evs in list(timeline_days.items())[:90]:  # cap to last 90 build-days
        try:
            d_obj = date.fromisoformat(d_iso)
            d_long = d_obj.strftime("%A, %B %-d, %Y")
        except ValueError:
            d_long = d_iso
        for ev in evs:
            badge_cls, label = _EVENT_BADGE[EventType(ev["event_type"])]
            ev["badge_class"] = badge_cls
            ev["label"] = label
            ev["cluster_slug"] = cluster_slug(ev["pathogen"], ev["pds_acc"])
        days_for_template.append({"date_long": d_long, "events": evs[:200]})

    first_date = (
        sorted(timeline_days.keys())[0] if timeline_days else run_date.isoformat()
    )
    try:
        first_date_long = date.fromisoformat(first_date).strftime("%B %-d, %Y")
    except ValueError:
        first_date_long = first_date

    rendered = env.get_template("timeline.html").render(
        **common,
        page_url="/timeline.html",
        days=days_for_template,
        total_events=len(history),
        timeline_days=90,
        first_date_long=first_date_long,
    )
    (site_dir / "timeline.html").write_text(rendered)
    log.info("Wrote timeline.html (%d events across %d days)", len(history), len(days_for_template))

    # ----- methods, data, about -----
    for page in ("methods", "data", "about"):
        rendered = env.get_template(f"{page}.html").render(
            **common, page_url=f"/{page}.html"
        )
        (site_dir / f"{page}.html").write_text(rendered)

    # ----- per-cluster pages -----
    # Only generate for human-containing clusters (otherwise the corpus is enormous).
    n_cluster_pages = 0
    # Build a history-by-cluster index for change logs
    history_by_cluster: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for h in history:
        key = (h["pathogen"], h["pds_acc"])
        history_by_cluster.setdefault(key, []).append(h)

    for pathogen, cmap in all_clusters_by_pathogen.items():
        for pds, c in cmap.items():
            if c.n_human < 1:
                continue
            hist_rows = []
            for h in sorted(
                history_by_cluster.get((pathogen, pds), []),
                key=lambda r: r["detected_date"],
                reverse=True,
            ):
                badge_cls, label = _EVENT_BADGE[EventType(h["event_type"])]
                hist_rows.append({
                    "date": h["detected_date"],
                    "badge_class": badge_cls,
                    "label": label,
                    "detail": h["summary"],
                })
            ctx = _prepare_cluster_detail(pathogen, c, hist_rows)
            cluster_common = dict(common)
            cluster_common["rel_root"] = "../"
            rendered = env.get_template("cluster.html").render(
                **cluster_common,
                page_url=f"/clusters/{ctx['slug']}.html",
                c=ctx,
            )
            (site_dir / "clusters" / f"{ctx['slug']}.html").write_text(rendered)
            n_cluster_pages += 1
    log.info("Wrote %d per-cluster pages", n_cluster_pages)

    # ----- data exports -----
    write_data_exports(site_dir, mixed, flat_events, tight_all_thresh, run_date)
    log.info("Wrote data exports")

    # ----- RSS -----
    write_rss(site_dir, history, run_date)
    log.info("Wrote feed.xml")

    # ----- copy CSS/JS assets from source tree -----
    src_assets = Path(__file__).parent / "assets"
    dst_assets = site_dir / "assets"
    if dst_assets.exists():
        shutil.rmtree(dst_assets)
    if src_assets.exists():
        shutil.copytree(src_assets, dst_assets)
        log.info("Copied assets from %s", src_assets)
    else:
        log.warning("No source assets directory at %s", src_assets)

    # GitHub Pages: .nojekyll prevents Jekyll processing
    (site_dir / ".nojekyll").write_text("")

    log.info("Site render complete -> %s", site_dir)
