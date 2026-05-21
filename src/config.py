"""Configuration for the NCBI Pathogen Detection alert system.

Centralizes everything tunable: which taxgroups to monitor, SNP thresholds
for near-neighbor calls, state file locations, and email settings. The four
target pathogens map to NCBI Pathogen Detection "taxgroup" names exactly as
they appear under ftp.ncbi.nlm.nih.gov/pathogen/Results/.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Pathogen taxgroups
# ---------------------------------------------------------------------------
# These names match the directory names under
# https://ftp.ncbi.nlm.nih.gov/pathogen/Results/
# Each taxgroup has its own nightly metadata snapshot.
PATHOGENS: dict[str, str] = {
    "Salmonella": "Salmonella",
    "STEC": "Escherichia_coli_Shigella",  # NCBI lumps STEC into E. coli / Shigella
    "Listeria": "Listeria",
    "Campylobacter": "Campylobacter",
}

# Base URL for Pathogen Detection results. Each taxgroup has a /latest_snps/
# symlink pointing to the most recent PDG release.
NCBI_BASE = "https://ftp.ncbi.nlm.nih.gov/pathogen/Results"

# ---------------------------------------------------------------------------
# Near-neighbor SNP thresholds
# ---------------------------------------------------------------------------
# Per user spec: report at three resolutions — outbreak-tight, PulseNet-standard,
# and broader surveillance. Each isolate's min_diff (SNPs to nearest non-clonal
# neighbor) is checked against each.
SNP_THRESHOLDS: list[int] = [5, 10, 50]

# ---------------------------------------------------------------------------
# Alert triggers
# ---------------------------------------------------------------------------
# All three are enabled per user spec. Kept as flags so individual triggers
# can be silenced in future without code changes.
ALERT_NEW_HUMAN_CLUSTER: bool = True       # New PDS cluster with ≥2 human cases
ALERT_NEW_HUMAN_IN_CLUSTER: bool = True    # Existing cluster gains a human isolate
ALERT_NONHUMAN_NEAR_HUMAN: bool = True     # Nonhuman isolate joins human cluster

MIN_HUMAN_CASES_NEW_CLUSTER: int = 2

# ---------------------------------------------------------------------------
# Epi-type classification
# ---------------------------------------------------------------------------
# NCBI's "epi_type" field is the primary signal. Values vary but cluster into:
HUMAN_EPI_TYPES: set[str] = {"clinical"}
# Anything not in HUMAN_EPI_TYPES and not blank is treated as nonhuman.
# Common nonhuman values: "environmental/other", "environmental", "food",
# "animal", "veterinary". We don't try to enumerate them all — we just check
# !is_human and not-null.

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
CACHE_DIR = REPO_ROOT / "cache"  # raw downloaded TSVs (gitignored)
DIGEST_DIR = REPO_ROOT / "digests"

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
# All read from environment — set as GitHub Actions secrets.
@dataclass
class EmailConfig:
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.environ.get("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", ""))
    smtp_pass: str = field(default_factory=lambda: os.environ.get("SMTP_PASS", ""))
    from_addr: str = field(default_factory=lambda: os.environ.get("ALERT_FROM", ""))
    to_addrs: list[str] = field(
        default_factory=lambda: [
            a.strip()
            for a in os.environ.get("ALERT_TO", "").split(",")
            if a.strip()
        ]
    )

    def is_configured(self) -> bool:
        return bool(
            self.smtp_host and self.smtp_user and self.smtp_pass
            and self.from_addr and self.to_addrs
        )


# ---------------------------------------------------------------------------
# Runtime knobs
# ---------------------------------------------------------------------------
# Cap how far back we consider a collection date "recent" for the digest.
# Older isolates can still appear in clusters but aren't headlined.
RECENT_WINDOW_DAYS: int = 90

# Cap the number of clusters shown per section in the digest to keep emails
# from becoming unreadable during high-activity weeks.
MAX_CLUSTERS_PER_SECTION: int = 25

# How many isolates to expand inside each cluster card.
MAX_ISOLATES_PER_CLUSTER: int = 15
