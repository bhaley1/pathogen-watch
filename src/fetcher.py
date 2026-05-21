"""Download the latest Pathogen Detection metadata snapshots.

NCBI Pathogen Detection publishes nightly snapshots at:
    https://ftp.ncbi.nlm.nih.gov/pathogen/Results/{taxgroup}/latest_snps/

Inside each release directory we want two files per taxgroup:
    Metadata/PDG{accession}.metadata.tsv     -> all isolate metadata
    Clusters/PDG{accession}.reference_target.SNP_distances.tsv  -> SNP matrix info
    Clusters/PDG{accession}.reference_target.cluster_list.tsv   -> PDS membership

In practice the metadata TSV already carries:
    PDT_acc, PDS_acc, target_acc, min_same, min_diff, epi_type, host,
    isolation_source, geo_loc_name, collection_date, scientific_name,
    serovar, AMR_genotypes, ...

That single file is sufficient for our alerting needs — we don't need to
download the full pairwise SNP matrix. We derive cluster membership and
near-neighbor counts from min_same / min_diff and PDS_acc.

The fetcher is resilient: it follows the `latest_snps` redirect, caches
downloaded files by release accession (so re-runs the same day are free),
and falls back to a previously-cached release if NCBI is unreachable.
"""

from __future__ import annotations

import gzip
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests

from . import config

log = logging.getLogger(__name__)

# Pattern for PDG release accessions e.g. PDG000000002.5210
PDG_RE = re.compile(r"PDG\d{9}\.\d+")

# NCBI is occasionally slow; be patient but not infinite.
REQUEST_TIMEOUT = 120
REQUEST_RETRIES = 3
RETRY_BACKOFF = 5  # seconds, exponential


@dataclass
class Snapshot:
    """One taxgroup's downloaded metadata snapshot."""

    pathogen: str           # user-facing name e.g. "Salmonella"
    taxgroup: str           # NCBI directory name e.g. "Salmonella"
    pdg_release: str        # e.g. "PDG000000002.5210"
    metadata_path: Path     # local path to the .tsv file
    fetched_at: float       # unix timestamp

    @property
    def is_fresh(self) -> bool:
        """Considered fresh if downloaded in the last 18 hours."""
        return (time.time() - self.fetched_at) < 18 * 3600


def _http_get(url: str, stream: bool = False) -> requests.Response:
    """GET with retry/backoff. Raises on final failure."""
    last_exc: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT, stream=stream)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            wait = RETRY_BACKOFF * (2 ** attempt)
            log.warning("GET %s failed (%s); retrying in %ds", url, e, wait)
            time.sleep(wait)
    raise RuntimeError(f"GET {url} failed after {REQUEST_RETRIES} attempts: {last_exc}")


def _resolve_latest_release(taxgroup: str) -> str:
    """Discover the current PDG release for a taxgroup.

    The `latest_snps/` path on NCBI redirects to the active release directory,
    but the directory listing is easier to parse than chasing redirects. We
    list the taxgroup root and pick the lexicographically largest PDG entry,
    which is also the most recent because of the zero-padded accession scheme.
    """
    listing_url = f"{config.NCBI_BASE}/{taxgroup}/"
    resp = _http_get(listing_url)
    releases = sorted(set(PDG_RE.findall(resp.text)))
    if not releases:
        raise RuntimeError(f"No PDG releases found at {listing_url}")
    latest = releases[-1]
    log.info("[%s] latest release: %s", taxgroup, latest)
    return latest


def _metadata_url(taxgroup: str, release: str) -> str:
    """Build the canonical metadata TSV URL for a release."""
    return (
        f"{config.NCBI_BASE}/{taxgroup}/{release}/Metadata/"
        f"{release}.metadata.tsv"
    )


def _download(url: str, dest: Path) -> Path:
    """Stream-download a (possibly large) file to dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _http_get(url, stream=True) as r, open(tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 16):
            if chunk:
                f.write(chunk)
    tmp.replace(dest)
    return dest


def _maybe_gunzip(path: Path) -> Path:
    """If we accidentally downloaded a .gz, decompress in place."""
    if path.suffix != ".gz":
        return path
    out = path.with_suffix("")
    with gzip.open(path, "rb") as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    return out


def fetch_snapshot(pathogen: str, taxgroup: str, cache_dir: Path | None = None) -> Snapshot:
    """Download (or reuse cached) latest metadata for one taxgroup.

    Files are cached by release accession so re-runs the same day are free.
    On NCBI failure, the most recent cached release is returned with a warning.
    """
    cache_dir = cache_dir or config.CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    tax_cache = cache_dir / taxgroup
    tax_cache.mkdir(exist_ok=True)

    try:
        release = _resolve_latest_release(taxgroup)
    except Exception as e:
        log.error("[%s] could not resolve latest release: %s", taxgroup, e)
        # Try fallback to most recent cached release
        cached = sorted(tax_cache.glob("PDG*.metadata.tsv"))
        if cached:
            fallback = cached[-1]
            log.warning("[%s] falling back to cached %s", taxgroup, fallback.name)
            release = PDG_RE.match(fallback.name).group(0)
            return Snapshot(
                pathogen=pathogen, taxgroup=taxgroup,
                pdg_release=release, metadata_path=fallback,
                fetched_at=fallback.stat().st_mtime,
            )
        raise

    local = tax_cache / f"{release}.metadata.tsv"
    if local.exists() and local.stat().st_size > 0:
        log.info("[%s] using cached %s", taxgroup, local.name)
    else:
        url = _metadata_url(taxgroup, release)
        log.info("[%s] downloading %s", taxgroup, url)
        _download(url, local)
        local = _maybe_gunzip(local)

    return Snapshot(
        pathogen=pathogen, taxgroup=taxgroup,
        pdg_release=release, metadata_path=local,
        fetched_at=time.time(),
    )


def fetch_all() -> list[Snapshot]:
    """Fetch snapshots for every configured pathogen.

    Errors on individual pathogens are logged but do not abort the run —
    a partial digest beats no digest.
    """
    snaps: list[Snapshot] = []
    for pathogen, taxgroup in config.PATHOGENS.items():
        try:
            snaps.append(fetch_snapshot(pathogen, taxgroup))
        except Exception as e:
            log.exception("[%s] fetch failed: %s", pathogen, e)
    return snaps
