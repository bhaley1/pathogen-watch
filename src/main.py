"""Main entry point: fetch → parse → cluster → diff → render site & email.

Run as:
  python -m src.main                  # full pipeline + site + email
  python -m src.main --no-email       # build site only, no email
  python -m src.main --no-site        # email only, skip site generation
  python -m src.main --no-state       # one-off; skip diff (no alert events)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from .joiner import load_pds_map, load_snp_distances, load_amr
from . import (
    config,
    fetcher,
    parser as ncbi_parser,
    cluster as cluster_mod,
    state,
    digest,
    email_send,
)
from .site import generator as site_gen

log = logging.getLogger("pathogen-watch")


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def run(
    do_email: bool = True,
    do_site: bool = True,
    use_state: bool = True,
    verbose: bool = False,
) -> int:
    setup_logging(verbose)
    run_date = date.today()
    log.info("=== pathogen-watch run: %s ===", run_date)

    snaps = fetcher.fetch_all()
    if not snaps:
        log.error("No snapshots fetched; aborting.")
        return 1

    events_by_p: dict[str, list[state.Event]] = {}
    mixed_by_p: dict[str, list[cluster_mod.Cluster]] = {}
    neighbors_by_p: dict[str, dict[int, list[cluster_mod.CrossClusterNearNeighbor]]] = {}
    all_clusters_by_p: dict[str, dict[str, cluster_mod.Cluster]] = {}
    pdg_releases: dict[str, str] = {}

    for snap in snaps:
        log.info("--- %s (release %s) ---", snap.pathogen, snap.pdg_release)
        pdg_releases[snap.pathogen] = snap.pdg_release
        stec_only = snap.pathogen == "STEC"
        pds_map = load_pds_map(snap.clusters_path)
        snp_map = load_snp_distances(snap.snp_distances_path)
        amr_map = load_amr(snap.amr_path)
        isolates = ncbi_parser.parse_to_list(
            snap.metadata_path, snap.pathogen,
            pds_map=pds_map, snp_map=snp_map, amr_map=amr_map,
            stec_only=stec_only,
        )
        clusters = cluster_mod.group_by_cluster(isolates)
        all_clusters_by_p[snap.pathogen] = clusters
        log.info("[%s] %d clusters total", snap.pathogen, len(clusters))

        mixed = cluster_mod.find_mixed_clusters(clusters)
        mixed_recent = cluster_mod.filter_recent_activity(mixed)
        mixed_recent.sort(
            key=lambda c: (c.latest_collection or date.min), reverse=True
        )
        mixed_by_p[snap.pathogen] = mixed_recent
        log.info(
            "[%s] %d mixed clusters (%d recent)",
            snap.pathogen, len(mixed), len(mixed_recent),
        )

        neighbors_by_p[snap.pathogen] = {
            t: cluster_mod.find_tight_nonhuman_neighbors(clusters, t)
            for t in config.SNP_THRESHOLDS
        }

        if use_state:
            prior = state.load_state(snap.pathogen)
            events = state.diff(snap.pathogen, prior, clusters)
            events_by_p[snap.pathogen] = events
            state.save_state(snap.pathogen, clusters, snap.pdg_release)
        else:
            events_by_p[snap.pathogen] = []

    # Site (does its own history appending and per-cluster rendering)
    if do_site:
        site_gen.render_site(
            run_date=run_date,
            events_by_pathogen=events_by_p,
            mixed_clusters_by_pathogen=mixed_by_p,
            near_neighbors_by_pathogen=neighbors_by_p,
            all_clusters_by_pathogen=all_clusters_by_p,
            pdg_releases=pdg_releases,
        )

    # Email digest (still useful as a notification channel)
    if do_email:
        text_body = digest.render_text(run_date, events_by_p, mixed_by_p, neighbors_by_p)
        html_body = digest.render_html(run_date, events_by_p, mixed_by_p, neighbors_by_p)

        config.DIGEST_DIR.mkdir(parents=True, exist_ok=True)
        (config.DIGEST_DIR / f"digest-{run_date.isoformat()}.txt").write_text(text_body)
        (config.DIGEST_DIR / f"digest-{run_date.isoformat()}.html").write_text(html_body)

        total_events = sum(len(v) for v in events_by_p.values())
        subject = f"[Pathogen Watch] {run_date.isoformat()} — {total_events} new event(s)"
        email_send.send_email(subject, text_body, html_body)

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pathogen Watch — daily NCBI surveillance")
    ap.add_argument("--no-email", action="store_true", help="Skip email send")
    ap.add_argument("--no-site", action="store_true", help="Skip site generation")
    ap.add_argument("--no-state", action="store_true", help="Skip state load/save")
    ap.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    args = ap.parse_args(argv)
    return run(
        do_email=not args.no_email,
        do_site=not args.no_site,
        use_state=not args.no_state,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
