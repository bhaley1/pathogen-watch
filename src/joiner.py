"""Join metadata.tsv with cluster_list, SNP_distances, and AMR files.

The metadata TSV gives us isolate-level info keyed by `target_acc` (= PDT_acc).
The other files map PDT_acc to:
  - PDS_acc (from cluster_list)
  - min_same, min_diff (from SNP_distances)
  - AMR genotype string (from AMR metadata)

We also reconstruct epi_type from host + isolation_source heuristics since
that column is no longer present in metadata.tsv for most pathogens.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def load_pds_map(path: Path | None) -> dict[str, str]:
    """PDT_acc -> PDS_acc."""
    if not path or not path.exists():
        return {}
    out: dict[str, str] = {}
    # cluster_list.tsv typically has columns like:
    #   PDS_acc  target_acc  ...
    # or:
    #   target_acc  PDS_acc
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames:
            return {}
        # Find which column has PDS and which has PDT
        pds_col = next((c for c in reader.fieldnames
                        if c.lower() in ("pds_acc", "snp_cluster", "cluster")), None)
        pdt_col = next((c for c in reader.fieldnames
                        if c.lower() in ("target_acc", "pdt_acc")), None)
        if not pds_col or not pdt_col:
            log.warning("cluster_list missing PDS/PDT columns; got %s", reader.fieldnames)
            return {}
        for row in reader:
            pds = (row.get(pds_col) or "").strip()
            pdt = (row.get(pdt_col) or "").strip()
            if pdt and pds and pds.upper() != "NULL":
                out[pdt] = pds
    log.info("Loaded %d PDT->PDS mappings from %s", len(out), path.name)
    return out


def load_snp_distances(path: Path | None) -> dict[str, tuple[int | None, int | None]]:
    """PDT_acc -> (min_same, min_diff)."""
    if not path or not path.exists():
        return {}
    out: dict[str, tuple[int | None, int | None]] = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames:
            return {}
        pdt_col = next((c for c in reader.fieldnames
                        if c.lower() in ("target_acc", "pdt_acc")), None)
        ms_col = next((c for c in reader.fieldnames
                       if c.lower() in ("min_same", "min_same_pds")), None)
        md_col = next((c for c in reader.fieldnames
                       if c.lower() in ("min_diff", "min_diff_pds")), None)
        if not pdt_col:
            log.warning("SNP_distances missing PDT column; got %s", reader.fieldnames)
            return {}
        for row in reader:
            pdt = (row.get(pdt_col) or "").strip()
            if not pdt:
                continue
            ms = _to_int(row.get(ms_col)) if ms_col else None
            md = _to_int(row.get(md_col)) if md_col else None
            out[pdt] = (ms, md)
    log.info("Loaded %d SNP-distance entries from %s", len(out), path.name)
    return out


def load_amr(path: Path | None) -> dict[str, str]:
    """PDT_acc -> AMR genotypes (semicolon-separated gene names)."""
    if not path or not path.exists():
        return {}
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames:
            return {}
        pdt_col = next((c for c in reader.fieldnames
                        if c.lower() in ("target_acc", "pdt_acc")), None)
        amr_col = next((c for c in reader.fieldnames
                        if c.lower() in ("amr_genotypes", "amr_genotypes_core",
                                         "genotype", "element_symbol")), None)
        if not pdt_col:
            log.warning("AMR file missing PDT column; got %s", reader.fieldnames)
            return {}
        # The AMR file is often row-per-gene; aggregate by PDT
        for row in reader:
            pdt = (row.get(pdt_col) or "").strip()
            if not pdt:
                continue
            if amr_col:
                gene = (row.get(amr_col) or "").strip()
                if gene:
                    if pdt in out:
                        out[pdt] = out[pdt] + ";" + gene
                    else:
                        out[pdt] = gene
            else:
                out.setdefault(pdt, "")
    log.info("Loaded AMR data for %d isolates from %s", len(out), path.name)
    return out


def _to_int(v):
    if v is None or v == "" or str(v).upper() == "NULL":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# -----------------------------------------------------------------------------
# epi_type reconstruction
# -----------------------------------------------------------------------------
# NCBI's metadata.tsv only carries epi_type for Campylobacter as of mid-2024+.
# For the other pathogens we infer it from host + isolation_source.

_HUMAN_HOST_TOKENS = {"homo sapiens", "human", "hu", "patient", "h. sapiens"}
_HUMAN_SOURCE_TOKENS = {
    "stool", "feces", "feaces", "blood", "csf", "urine", "wound",
    "sputum", "respiratory", "throat", "clinical", "patient",
}
_NONHUMAN_SOURCE_TOKENS_FOOD = {
    "beef", "poultry", "chicken", "pork", "swine", "bovine", "cattle",
    "dairy", "milk", "cheese", "egg", "produce", "lettuce", "spinach",
    "deli", "fish", "seafood", "shrimp", "salmon", "turkey",
}
_NONHUMAN_SOURCE_TOKENS_ENV = {
    "water", "soil", "sediment", "environmental", "swab", "drain",
    "floor", "compost", "manure", "wastewater",
}


def infer_epi_type(host: str | None, isolation_source: str | None) -> str | None:
    """Best-effort epi_type inference. Returns 'clinical', 'environmental/other',
    or None if we can't tell."""
    h = (host or "").lower().strip()
    s = (isolation_source or "").lower().strip()
    if any(t in h for t in _HUMAN_HOST_TOKENS):
        return "clinical"
    if any(t in s for t in _HUMAN_SOURCE_TOKENS):
        return "clinical"
    if any(t in s for t in _NONHUMAN_SOURCE_TOKENS_FOOD):
        return "environmental/other"
    if any(t in s for t in _NONHUMAN_SOURCE_TOKENS_ENV):
        return "environmental/other"
    if h and h not in _HUMAN_HOST_TOKENS:
        # Non-empty host that isn't human -> nonhuman
        return "environmental/other"
    return None
