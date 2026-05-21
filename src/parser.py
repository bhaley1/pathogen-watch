"""Parse Pathogen Detection metadata TSVs into isolate records.

The NCBI metadata TSV has ~50 columns; we project down to what's needed for
clustering/alerting. Column names have shifted over time (e.g. `epi_type`
was once `serovar_epi_type`), so we look up by a list of candidate names and
treat anything missing as null.

Date parsing is permissive — NCBI accepts everything from "2024" to
"2024-03-15T12:00:00Z". We coerce to a date-or-None.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from . import config

log = logging.getLogger(__name__)

# Column name candidates, in priority order. First non-empty match wins.
COL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "pdt_acc": ("target_acc", "PDT_acc", "pdt_acc"),
    "pds_acc": ("PDS_acc", "pds_acc", "PDS_acc_in_latest_PDG",
                "SNP_cluster", "snp_cluster", "PDS_acc.1"),
    "epi_type": ("epi_type", "serovar_epi_type"),
    "host": ("host", "host_scientific_name"),
    "isolation_source": ("isolation_source", "source"),
    "geo_loc_name": ("geo_loc_name", "country", "location"),
    "collection_date": ("collection_date", "collected_date"),
    "scientific_name": ("scientific_name", "organism", "Organism group"),
    "serovar": ("serovar", "serotype"),
    "min_same": ("min_same", "min_same_PDS"),
    "min_diff": ("min_diff", "min_diff_PDS"),
    "amr_genotypes": ("AMR_genotypes", "amr_genotypes", "AMR_genotypes_core"),
    "biosample_acc": ("biosample_acc", "BioSample", "biosample"),
    "asm_acc": ("asm_acc", "assembly_acc"),
    "sra_acc": ("Run", "sra_acc", "Run_acc"),
}


@dataclass
class Isolate:
    """One row from the metadata TSV, projected to fields we use."""

    pdt_acc: str
    pds_acc: str | None
    epi_type: str | None
    host: str | None
    isolation_source: str | None
    geo_loc_name: str | None
    collection_date: date | None
    scientific_name: str | None
    serovar: str | None
    min_same: int | None        # SNPs to nearest isolate in same PDS cluster
    min_diff: int | None        # SNPs to nearest isolate in a different PDS cluster
    amr_genotypes: str | None
    biosample_acc: str | None
    asm_acc: str | None
    sra_acc: str | None
    pathogen: str = ""          # filled in by caller (Salmonella, STEC, etc.)

    @property
    def is_human(self) -> bool:
        return (self.epi_type or "").strip().lower() in config.HUMAN_EPI_TYPES

    @property
    def is_nonhuman(self) -> bool:
        et = (self.epi_type or "").strip().lower()
        return bool(et) and et not in config.HUMAN_EPI_TYPES

    @property
    def has_cluster(self) -> bool:
        return bool(self.pds_acc) and self.pds_acc.upper() != "NULL"

    def country(self) -> str | None:
        """Extract country from 'USA: Maryland' style geo strings."""
        if not self.geo_loc_name:
            return None
        return self.geo_loc_name.split(":")[0].strip() or None


def _first_present(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    for n in names:
        v = row.get(n)
        if v is not None and v != "" and v.upper() != "NULL":
            return v
    return None


def _to_int(v: str | None) -> int | None:
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_date(v: str | None) -> date | None:
    """Parse NCBI's many date formats; return None on failure."""
    if not v:
        return None
    s = v.strip()
    # Strip time portion if present
    if "T" in s:
        s = s.split("T", 1)[0]
    # Try common formats from most-specific to least
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y/%m/%d", "%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_metadata(path: Path, pathogen: str) -> Iterable[Isolate]:
    """Yield Isolate objects from a metadata TSV.

    Streaming is intentional — Salmonella metadata is ~600k rows and we don't
    want to hold the whole thing in memory unless the caller decides to.
    """
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        log.info("[%s] TSV columns: %s", pathogen, ", ".join((reader.fieldnames or [])[:50]))
        for row in reader:
            pdt = _first_present(row, COL_CANDIDATES["pdt_acc"])
            if not pdt:
                continue  # rows without a PDT_acc are unusable
            yield Isolate(
                pdt_acc=pdt,
                pds_acc=_first_present(row, COL_CANDIDATES["pds_acc"]),
                epi_type=_first_present(row, COL_CANDIDATES["epi_type"]),
                host=_first_present(row, COL_CANDIDATES["host"]),
                isolation_source=_first_present(row, COL_CANDIDATES["isolation_source"]),
                geo_loc_name=_first_present(row, COL_CANDIDATES["geo_loc_name"]),
                collection_date=_to_date(_first_present(row, COL_CANDIDATES["collection_date"])),
                scientific_name=_first_present(row, COL_CANDIDATES["scientific_name"]),
                serovar=_first_present(row, COL_CANDIDATES["serovar"]),
                min_same=_to_int(_first_present(row, COL_CANDIDATES["min_same"])),
                min_diff=_to_int(_first_present(row, COL_CANDIDATES["min_diff"])),
                amr_genotypes=_first_present(row, COL_CANDIDATES["amr_genotypes"]),
                biosample_acc=_first_present(row, COL_CANDIDATES["biosample_acc"]),
                asm_acc=_first_present(row, COL_CANDIDATES["asm_acc"]),
                sra_acc=_first_present(row, COL_CANDIDATES["sra_acc"]),
                pathogen=pathogen,
            )


def parse_to_list(path: Path, pathogen: str, stec_only: bool = False) -> list[Isolate]:
    """Materialize the iterator and optionally filter to STEC pathotype only.

    For the Escherichia_coli_Shigella taxgroup, the user only wants STEC. We
    filter on AMR/virulence gene hits for stx1/stx2 in the AMR_genotypes
    column, which Pathogen Detection populates from AMRFinderPlus.
    """
    out: list[Isolate] = []
    for iso in parse_metadata(path, pathogen):
        if stec_only:
            amr = (iso.amr_genotypes or "").lower()
            # stx1A/stx1B/stx2A/stx2B variants all start with 'stx'
            if "stx" not in amr:
                continue
        out.append(iso)
    log.info("[%s] parsed %d isolates from %s", pathogen, len(out), path.name)
    return out
