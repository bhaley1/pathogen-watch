"""Group isolates into SNP clusters and identify alerting conditions.

A "PDS cluster" is NCBI's nightly-recomputed SNP-based grouping at a single
threshold (currently 50 SNPs for most taxgroups). Within that, we use each
isolate's `min_same` to know how tightly it sits with its nearest neighbor
in the same cluster, and `min_diff` for its nearest neighbor outside the
cluster. Combined with `epi_type`, that's enough to detect:

  1. Clusters containing ≥1 human + ≥1 nonhuman isolate (mixed clusters)
  2. Within a mixed cluster, the tightest human↔nonhuman pairing is
     bounded above by min_same of the human isolate
  3. Isolates whose nearest *cross-cluster* neighbor is within a tight SNP
     threshold (potential cluster-merging candidates)

We don't have pairwise distances at hand without downloading the full SNP
matrix per cluster, so within-cluster human↔nonhuman near-neighbor calls
are best-effort: we use the minimum of `min_same` over the human isolates
as an upper bound on the closest human↔X distance, where X is any other
isolate in the cluster.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from . import config
from .parser import Isolate

log = logging.getLogger(__name__)


@dataclass
class Cluster:
    """A PDS cluster summarized for alerting."""

    pds_acc: str
    pathogen: str
    isolates: list[Isolate] = field(default_factory=list)

    @property
    def n_total(self) -> int:
        return len(self.isolates)

    @property
    def humans(self) -> list[Isolate]:
        return [i for i in self.isolates if i.is_human]

    @property
    def nonhumans(self) -> list[Isolate]:
        return [i for i in self.isolates if i.is_nonhuman]

    @property
    def n_human(self) -> int:
        return len(self.humans)

    @property
    def n_nonhuman(self) -> int:
        return len(self.nonhumans)

    @property
    def is_mixed(self) -> bool:
        return self.n_human >= 1 and self.n_nonhuman >= 1

    @property
    def tightest_human_min_same(self) -> int | None:
        """Min `min_same` among human isolates — upper bound on closest
        human↔X distance within the cluster."""
        vals = [h.min_same for h in self.humans if h.min_same is not None]
        return min(vals) if vals else None

    @property
    def latest_collection(self) -> date | None:
        dates = [i.collection_date for i in self.isolates if i.collection_date]
        return max(dates) if dates else None

    @property
    def countries(self) -> set[str]:
        return {c for i in self.isolates if (c := i.country())}

    @property
    def nonhuman_sources(self) -> set[str]:
        """Distinct isolation-source / host labels among nonhuman isolates."""
        out: set[str] = set()
        for i in self.nonhumans:
            label = i.isolation_source or i.host
            if label:
                out.add(label)
        return out

    def url(self) -> str:
        """User-facing NCBI Pathogen Detection cluster page."""
        return f"https://www.ncbi.nlm.nih.gov/pathogens/isolates/#{self.pds_acc}"


@dataclass
class CrossClusterNearNeighbor:
    """A nonhuman isolate whose nearest cross-cluster neighbor is tight,
    AND that nearest-cluster (or its own) contains humans. This catches the
    'about to merge into a human cluster' case that pure within-cluster
    grouping misses."""

    isolate: Isolate
    threshold: int  # which SNP threshold was crossed (5, 10, 50)


def group_by_cluster(isolates: Iterable[Isolate]) -> dict[str, Cluster]:
    """Bucket isolates by PDS_acc. Isolates without a cluster are dropped —
    they have no neighbors-by-cluster to report."""
    by_pds: dict[str, Cluster] = {}
    for iso in isolates:
        if not iso.has_cluster:
            continue
        c = by_pds.get(iso.pds_acc)
        if c is None:
            c = Cluster(pds_acc=iso.pds_acc, pathogen=iso.pathogen)
            by_pds[iso.pds_acc] = c
        c.isolates.append(iso)
    return by_pds


def find_mixed_clusters(clusters: dict[str, Cluster]) -> list[Cluster]:
    """Clusters that contain both human and nonhuman isolates."""
    return [c for c in clusters.values() if c.is_mixed]


def find_human_clusters(clusters: dict[str, Cluster], min_humans: int = 2) -> list[Cluster]:
    """Clusters with at least `min_humans` human isolates (regardless of
    nonhuman presence). These are the 'investigate this outbreak' set."""
    return [c for c in clusters.values() if c.n_human >= min_humans]


def find_tight_nonhuman_neighbors(
    clusters: dict[str, Cluster],
    threshold: int,
) -> list[CrossClusterNearNeighbor]:
    """Nonhuman isolates sitting at ≤threshold SNPs to a different PDS.

    These are early-warning signals: the isolate isn't in a human cluster
    *yet*, but it's close enough to one that next week's PDS recomputation
    could merge them. We don't currently verify that the *neighboring*
    cluster is human-containing (would need the full SNP matrix); the
    headline value is 'this nonhuman is suspiciously close to something
    else'.
    """
    out: list[CrossClusterNearNeighbor] = []
    for c in clusters.values():
        for iso in c.nonhumans:
            if iso.min_diff is not None and iso.min_diff <= threshold:
                out.append(CrossClusterNearNeighbor(isolate=iso, threshold=threshold))
    # Sort tightest first
    out.sort(key=lambda n: (n.isolate.min_diff or 0))
    return out


def filter_recent_activity(
    clusters: list[Cluster],
    window_days: int = config.RECENT_WINDOW_DAYS,
    today: date | None = None,
) -> list[Cluster]:
    """Keep only clusters with at least one isolate collected in the window."""
    today = today or date.today()
    cutoff = today - timedelta(days=window_days)
    return [c for c in clusters if c.latest_collection and c.latest_collection >= cutoff]
