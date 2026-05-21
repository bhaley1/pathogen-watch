"""Site generation configuration."""

from __future__ import annotations

import os
from pathlib import Path

from .. import config as core_config

# ---------------------------------------------------------------------------
# Site identity
# ---------------------------------------------------------------------------
SITE_TITLE = "Pathogen Watch"
SITE_TAGLINE = "Daily SNP-cluster surveillance from NCBI Pathogen Detection"
SITE_DESCRIPTION = (
    "Open-access daily surveillance of human–animal–environmental "
    "genomic clusters of Salmonella, STEC, Listeria, and Campylobacter."
)
SITE_AUTHOR = "Bradd Haley, PhD"
SITE_AFFILIATION = "USDA Agricultural Research Service"

# The base URL of the deployed site. Read from env so workflow can pass
# either bhaley1.github.io/pathogen-watch or a custom domain later.
SITE_BASE_URL = os.environ.get(
    "SITE_BASE_URL", "https://bhaley1.github.io/pathogen-watch"
).rstrip("/")

# ---------------------------------------------------------------------------
# Giscus comment widget
# ---------------------------------------------------------------------------
# All values are public (it's a frontend widget). Configure these once via
# https://giscus.app, then set them as repo variables (NOT secrets — they're
# meant to be in HTML source).
GISCUS = {
    "repo": os.environ.get("GISCUS_REPO", "bhaley1/pathogen-watch"),
    "repo_id": os.environ.get("GISCUS_REPO_ID", ""),
    "category": os.environ.get("GISCUS_CATEGORY", "Cluster discussions"),
    "category_id": os.environ.get("GISCUS_CATEGORY_ID", ""),
}

def giscus_enabled() -> bool:
    return bool(GISCUS["repo_id"] and GISCUS["category_id"])

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SITE_DIR = core_config.REPO_ROOT / "site"
TEMPLATES_DIR = core_config.REPO_ROOT / "templates"

# Subdirectories of site/
CLUSTER_PAGES_DIR = SITE_DIR / "clusters"
DATA_DIR = SITE_DIR / "data"
ASSETS_DIR = SITE_DIR / "assets"

# ---------------------------------------------------------------------------
# Display knobs
# ---------------------------------------------------------------------------
# How many recent alerts to show on the timeline page total
TIMELINE_LENGTH = 200
# How many days of alerts to surface on the front page
FRONT_PAGE_RECENT_DAYS = 14
