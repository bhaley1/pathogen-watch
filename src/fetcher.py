"""Download NCBI Pathogen Detection nightly snapshots.

The release directory layout (as of NCBI's 2024+ reorganization):

    Results/{taxgroup}/PDG{X.Y}/
        Metadata/   PDG{X.Y}.metadata.tsv              (base isolate metadata)
        Clusters/   PDG{X.Y}.reference_target.cluster_list.tsv     (PDT -> PDS)
                    PDG{X.Y}.reference_target.SNP_distances.tsv    (min_same/min_diff)
        AMR/        PDG{X.Y}.amr.metadata.tsv          (AMR/virulence genotypes)

We fetch all four and the parser merges them by target_acc / PDT_acc.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

PDG_RE = re.compile(r"PDG\d+\.\d+")


@dataclass
class Snapshot:
    pathogen: str
    taxgroup: str
    pdg_release: str
    metadata_path: Path
    clusters_path: Path | None
    snp_distances_path: Path | None
    amr_path: Path | None


def _latest_release(taxgroup: str) -> str | None:
    url = f"{config.NCBI_BASE}/{taxgroup}/"
    log.debug("Listing %s", url)
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except Exception as e:
        log.error("[%s] failed to list releases: %s", taxgroup, e)
        return None
    matches = PDG_RE.findall(r.text)
    if not matches:
        log.error("[%s] no PDG releases found at %s", taxgroup, url)
        return None
    # Pick the lexically highest (release numbers monotonically increase)
    latest = sorted(set(matches), key=lambda s: tuple(int(x) for x in s[3:].split('.')))[-1]
    return latest


def _download(url: str, dest: Path, required: bool = True) -> Path | None:
    """Stream-download a file. If required=False, missing files return None."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=300) as r:
            if r.status_code == 404:
                if required:
                    raise FileNotFoundError(url)
                log.warning("Optional file 404: %s", url)
                return None
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    f.write(chunk)
        return dest
    except Exception as e:
        if required:
            raise
        log.warning("Optional file fetch failed (%s): %s", url, e)
        return None


def _fetch_one(pathogen: str, taxgroup: str) -> Snapshot | None:
    release = _latest_release(taxgroup)

    # PROBE for Salmonella: list its Clusters/ directory and log every file
    # name so we can find where PDS cluster info now lives.
    if pathogen == "Salmonella" and release:
        try:
            probe_url = f"{config.NCBI_BASE}/{taxgroup}/{release}/Clusters/"
            log.info("[Salmonella] probing Clusters dir: %s", probe_url)
            r = requests.get(probe_url, timeout=60)
            if r.status_code == 200:
                # Crude parse: pull every href that ends in .tsv
                import re as _re
                files = _re.findall(r'href="([^"]+\.tsv)"', r.text)
                log.info("[Salmonella] files in Clusters/: %s", files or "(none)")
            else:
                log.warning("[Salmonella] Clusters dir status: %d", r.status_code)
            # Also probe alternate directories
            for alt in ["SNP_trees/", "Cluster_data/", "Trees/", ""]:
                alt_url = f"{config.NCBI_BASE}/{taxgroup}/{release}/{alt}"
                ar = requests.get(alt_url, timeout=60)
                if ar.status_code == 200 and alt:
                    files = _re.findall(r'href="([^"]+\.tsv)"', ar.text)
                    if files:
                        log.info("[Salmonella] found .tsv files in %s: %s", alt, files)
        except Exception as e:
            log.warning("[Salmonella] probe failed: %s", e)


    # PROBE for Salmonella: list its Clusters/ directory and log every file
    # name so we can find where PDS cluster info now lives.
    if pathogen == "Salmonella" and release:
        try:
            probe_url = f"{config.NCBI_BASE}/{taxgroup}/{release}/Clusters/"
            log.info("[Salmonella] probing Clusters dir: %s", probe_url)
            r = requests.get(probe_url, timeout=60)
            if r.status_code == 200:
                # Crude parse: pull every href that ends in .tsv
                import re as _re
                files = _re.findall(r'href="([^"]+\.tsv)"', r.text)
                log.info("[Salmonella] files in Clusters/: %s", files or "(none)")
            else:
                log.warning("[Salmonella] Clusters dir status: %d", r.status_code)
            # Also probe alternate directories
            for alt in ["SNP_trees/", "Cluster_data/", "Trees/", ""]:
                alt_url = f"{config.NCBI_BASE}/{taxgroup}/{release}/{alt}"
                ar = requests.get(alt_url, timeout=60)
                if ar.status_code == 200 and alt:
                    files = _re.findall(r'href="([^"]+\.tsv)"', ar.text)
                    if files:
                        log.info("[Salmonella] found .tsv files in %s: %s", alt, files)
        except Exception as e:
            log.warning("[Salmonella] probe failed: %s", e)

    if not release:
        # Try to fall back to cached release on disk
        tax_cache = config.CACHE_DIR / taxgroup
        cached = sorted(tax_cache.glob("PDG*.metadata.tsv")) if tax_cache.exists() else []
        if cached:
            fallback = cached[-1]
            log.warning("[%s] no remote release; falling back to cached %s",
                        taxgroup, fallback.name)
            release = PDG_RE.match(fallback.name).group(0)
        else:
            return None

    log.info("[%s] latest release: %s", pathogen, release)
    tax_cache = config.CACHE_DIR / taxgroup
    base_url = f"{config.NCBI_BASE}/{taxgroup}/{release}"

    # ---- Required: metadata.tsv ----
    md_local = tax_cache / f"{release}.metadata.tsv"
    if md_local.exists() and md_local.stat().st_size > 0:
        log.info("[%s] using cached %s", pathogen, md_local.name)
    else:
        url = f"{base_url}/Metadata/{release}.metadata.tsv"
        log.info("[%s] downloading %s", pathogen, url)
        _download(url, md_local, required=True)

    # ---- Optional: cluster_list.tsv (PDT -> PDS) ----
    # NCBI's actual filename may vary slightly; try the canonical name first,
    # then a couple of alternates. Cache each successful fetch.
    cl_local = tax_cache / f"{release}.cluster_list.tsv"
    if not (cl_local.exists() and cl_local.stat().st_size > 0):
        candidates = [
            f"{base_url}/Clusters/{release}.reference_target.cluster_list.tsv",
            f"{base_url}/Clusters/{release}.cluster_list.tsv",
            f"{base_url}/Clusters/{release}.reference_target.clusters.tsv",
        ]
        for url in candidates:
            log.info("[%s] trying cluster_list: %s", pathogen, url)
            got = _download(url, cl_local, required=False)
            if got and got.stat().st_size > 0:
                log.info("[%s] cluster_list downloaded (%d bytes)", pathogen, got.stat().st_size)
                break
        else:
            cl_local = None  # no cluster file available
    else:
        log.info("[%s] using cached cluster_list", pathogen)

    # ---- SNP_distances: NOT downloaded ----
    # NCBI's SNP_distances.tsv is a pairwise distance file (rows are pairs of
    # isolates) and can be 15+ GB. We do not need it: per-isolate min_same and
    # min_diff are now inline in metadata.tsv as 'minsame'/'mindiff'.
    sd_local = None

    # ---- Optional: AMR metadata ----
    amr_local = tax_cache / f"{release}.amr.metadata.tsv"
    if not (amr_local.exists() and amr_local.stat().st_size > 0):
        candidates = [
            f"{base_url}/AMR/{release}.amr.metadata.tsv",
            f"{base_url}/AMRFinderPlus/{release}.amr.metadata.tsv",
        ]
        for url in candidates:
            log.info("[%s] trying AMR metadata: %s", pathogen, url)
            got = _download(url, amr_local, required=False)
            if got and got.stat().st_size > 0:
                log.info("[%s] AMR metadata downloaded (%d bytes)", pathogen, got.stat().st_size)
                break
        else:
            amr_local = None
    else:
        log.info("[%s] using cached AMR metadata", pathogen)

    return Snapshot(
        pathogen=pathogen,
        taxgroup=taxgroup,
        pdg_release=release,
        metadata_path=md_local,
        clusters_path=cl_local,
        snp_distances_path=sd_local,
        amr_path=amr_local,
    )


def fetch_all() -> list[Snapshot]:
    out: list[Snapshot] = []
    for pathogen, taxgroup in config.PATHOGENS.items():
        snap = _fetch_one(pathogen, taxgroup)
        if snap:
            out.append(snap)
        time.sleep(1)  # be polite to NCBI
    return out
