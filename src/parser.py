"""Parse NCBI Pathogen Detection metadata TSV and join companion files.

The parser materializes per-isolate Isolate records by:
  1. Iterating the (large) metadata.tsv
  2. Looking up PDS_acc from cluster_list (sidecar file)
  3. Looking up min_same/min_diff from SNP_distances (sidecar file)
  4. Looking up AMR genotypes from amr.metadata (sidecar file)
  5. Inferring epi_type from host/isolation_source where missing
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

log = logging.getLogger(__name__)


# Columns in the new metadata.tsv. We keep candidates because field names
# still drift between releases.
COL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "pdt_acc": ("target_acc", "PDT_acc", "pdt_acc"),
    "pds_acc": ("PDS_acc", "pds_acc", "computed_types"),  # rare inline cases
    "epi_type": ("epi_type", "serovar_epi_type"),
    "host": ("host", "host_scientific_name"),
    "isolation_source": ("isolation_source", "source"),
    "geo_loc_name": ("geo_loc_name", "country", "location"),
    "collection_date": ("collection_date", "collected_date"),
    "scientific_name": ("scientific_name", "organism", "Organism group"),
    "serovar": ("serovar", "serotype"),
    "biosample_acc": ("biosample_acc", "BioSample", "biosample"),
    "asm_acc": ("asm_acc", "assembly_acc"),
    "sra_acc": ("Run", "sra_acc", "Run_acc"),
    # New as of NCBI's 2024 schema: per-isolate SNP distance fields are now
    # inline in metadata.tsv (note: no underscore in the new names).
    "min_same": ("minsame", "min_same", "min_same_PDS"),
    "min_diff": ("mindiff", "min_diff", "min_diff_PDS"),
}


@dataclass
class Isolate:
    pdt_acc: str
    pds_acc: str | None
    epi_type: str | None
    host: str | None
    isolation_source: str | None
    geo_loc_name: str | None
    collection_date: date | None
    scientific_name: str | None
    serovar: str | None
    min_same: int | None
    min_diff: int | None
    amr_genotypes: str | None
    biosample_acc: str | None
    asm_acc: str | None
    sra_acc: str | None
    pathogen: str

    @property
    def has_cluster(self) -> bool:
        return bool(self.pds_acc) and self.pds_acc.upper() != "NULL"

    @property
    def is_human(self) -> bool:
        return (self.epi_type or "").lower() == "clinical"

    @property
    def is_nonhuman(self) -> bool:
        return bool(self.epi_type) and not self.is_human

    def country(self) -> str | None:
        if not self.geo_loc_name:
            return None
        m = re.match(r"^\s*([^:,]+)", self.geo_loc_name)
        return m.group(1).strip() if m else None


def _first_present(row: dict, names: tuple[str, ...]) -> str | None:
    for n in names:
        v = row.get(n)
        if v not in (None, "", "NULL"):
            return v.strip() if isinstance(v, str) else v
    return None


def _to_date(s: str | None) -> date | None:
    if not s or s == "NULL":
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None




def _to_int(v):
    if v is None or v == "" or str(v).upper() == "NULL":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def iter_isolates(
    metadata_path: Path,
    pathogen: str,
    pds_map: dict[str, str],
    snp_map: dict[str, tuple[int | None, int | None]],
    amr_map: dict[str, str],
) -> Iterator[Isolate]:
    """Yield Isolate records, joining the sidecar maps."""
    # Avoid circular import — joiner is small
    from .joiner import infer_epi_type

    with open(metadata_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        log.info("[%s] TSV columns: %s", pathogen,
                 ", ".join((reader.fieldnames or [])[:50]))
        for row in reader:
            pdt = _first_present(row, COL_CANDIDATES["pdt_acc"])
            if not pdt:
                continue

            host = _first_present(row, COL_CANDIDATES["host"])
            iso_src = _first_present(row, COL_CANDIDATES["isolation_source"])
            epi = _first_present(row, COL_CANDIDATES["epi_type"])
            if not epi:
                epi = infer_epi_type(host, iso_src)

            # Prefer inline metadata values; fall back to snp_map (legacy sidecar)
            ms_inline = _to_int(_first_present(row, COL_CANDIDATES["min_same"]))
            md_inline = _to_int(_first_present(row, COL_CANDIDATES["min_diff"]))
            ms_sidecar, md_sidecar = snp_map.get(pdt, (None, None))
            ms = ms_inline if ms_inline is not None else ms_sidecar
            md = md_inline if md_inline is not None else md_sidecar

            # Same idea for pds_acc — prefer the sidecar map (cluster_list)
            # but fall back to inline metadata if it appears as a column
            inline_pds = _first_present(row, COL_CANDIDATES["pds_acc"])
            pds_value = pds_map.get(pdt) or (inline_pds if (inline_pds and inline_pds.startswith("PDS")) else None)

            yield Isolate(
                pdt_acc=pdt,
                pds_acc=pds_value,
                epi_type=epi,
                host=host,
                isolation_source=iso_src,
                geo_loc_name=_first_present(row, COL_CANDIDATES["geo_loc_name"]),
                collection_date=_to_date(_first_present(row, COL_CANDIDATES["collection_date"])),
                scientific_name=_first_present(row, COL_CANDIDATES["scientific_name"]),
                serovar=_first_present(row, COL_CANDIDATES["serovar"]),
                min_same=ms,
                min_diff=md,
                amr_genotypes=amr_map.get(pdt),
                biosample_acc=_first_present(row, COL_CANDIDATES["biosample_acc"]),
                asm_acc=_first_present(row, COL_CANDIDATES["asm_acc"]),
                sra_acc=_first_present(row, COL_CANDIDATES["sra_acc"]),
                pathogen=pathogen,
            )


def parse_to_list(
    metadata_path: Path,
    pathogen: str,
    pds_map: dict[str, str] | None = None,
    snp_map: dict[str, tuple[int | None, int | None]] | None = None,
    amr_map: dict[str, str] | None = None,
    stec_only: bool = False,
) -> list[Isolate]:
    """Materialize isolates. For STEC, filter to stx-positive only."""
    pds_map = pds_map or {}
    snp_map = snp_map or {}
    amr_map = amr_map or {}

    isolates = list(iter_isolates(metadata_path, pathogen, pds_map, snp_map, amr_map))
    log.info("[%s] parsed %d isolates from %s", pathogen, len(isolates), metadata_path.name)

    if stec_only:
        kept = [
            i for i in isolates
            if i.amr_genotypes and re.search(r"(stx[ab]?[12]?|shiga[_\s-]?toxin)", i.amr_genotypes, re.IGNORECASE)
        ]
        log.info("[%s] filtered to %d stx-positive isolates", pathogen, len(kept))
        isolates = kept

    n_clustered = sum(1 for i in isolates if i.has_cluster)
    n_human = sum(1 for i in isolates if i.is_human)
    log.info("[%s] %d/%d isolates in clusters; %d are clinical",
             pathogen, n_clustered, len(isolates), n_human)
    return isolates
