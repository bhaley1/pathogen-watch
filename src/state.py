"""Persistent state and day-over-day diffing.

Each daily run writes a compact JSON snapshot of every cluster it observed
(per pathogen) to state/{pathogen}.json. The next run loads the previous
snapshot, compares, and emits three event types:

  - NEW_HUMAN_CLUSTER   : PDS not in prior snapshot, has ≥2 humans now
  - NEW_HUMAN_IN_CLUSTER: PDS seen before but human count increased
  - NEW_NONHUMAN_NEAR_HUMAN: human-containing PDS gained a nonhuman isolate

Storing only the compact summary (not full isolate metadata) keeps the
state directory git-friendly and makes diffs readable in PR review.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from . import config
from .cluster import Cluster
from .parser import Isolate

log = logging.getLogger(__name__)

STATE_VERSION = 1


class EventType(str, Enum):
    NEW_HUMAN_CLUSTER = "new_human_cluster"
    NEW_HUMAN_IN_CLUSTER = "new_human_in_cluster"
    NEW_NONHUMAN_NEAR_HUMAN = "new_nonhuman_near_human"


@dataclass
class ClusterSnapshot:
    """Compact serializable cluster summary stored in state."""

    pds_acc: str
    n_human: int
    n_nonhuman: int
    human_pdt_accs: list[str]
    nonhuman_pdt_accs: list[str]
    tightest_human_min_same: int | None
    latest_collection: str | None  # ISO date or None

    @classmethod
    def from_cluster(cls, c: Cluster) -> "ClusterSnapshot":
        return cls(
            pds_acc=c.pds_acc,
            n_human=c.n_human,
            n_nonhuman=c.n_nonhuman,
            human_pdt_accs=sorted(h.pdt_acc for h in c.humans),
            nonhuman_pdt_accs=sorted(n.pdt_acc for n in c.nonhumans),
            tightest_human_min_same=c.tightest_human_min_same,
            latest_collection=c.latest_collection.isoformat() if c.latest_collection else None,
        )


@dataclass
class Event:
    """A single alertable change."""

    event_type: EventType
    pathogen: str
    pds_acc: str
    summary: str
    # New isolate PDT_accs that triggered this event (if applicable)
    new_pdt_accs: list[str] = None  # type: ignore

    def to_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d


def state_path(pathogen: str, state_dir: Path | None = None) -> Path:
    state_dir = state_dir or config.STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    # Sanitize pathogen name for filename
    safe = pathogen.replace("/", "_").replace(" ", "_")
    return state_dir / f"{safe}.json"


def load_state(pathogen: str, state_dir: Path | None = None) -> dict[str, ClusterSnapshot]:
    """Load prior snapshot. Returns empty dict on first run."""
    p = state_path(pathogen, state_dir)
    if not p.exists():
        log.info("[%s] no prior state at %s; first run", pathogen, p)
        return {}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        log.error("[%s] corrupt state file %s: %s; treating as first run", pathogen, p, e)
        return {}
    if data.get("version") != STATE_VERSION:
        log.warning("[%s] state version mismatch; ignoring", pathogen)
        return {}
    return {
        pds: ClusterSnapshot(**snap)
        for pds, snap in data.get("clusters", {}).items()
    }


def save_state(
    pathogen: str,
    clusters: dict[str, Cluster],
    pdg_release: str,
    state_dir: Path | None = None,
) -> Path:
    """Write current snapshot atomically.

    Only mixed and ≥2-human clusters are persisted. Everything else is noise
    we don't need to diff against, and keeping the state file small matters
    for git history.
    """
    keep = {
        pds: ClusterSnapshot.from_cluster(c)
        for pds, c in clusters.items()
        if c.n_human >= 1  # any human presence is worth tracking
    }
    payload = {
        "version": STATE_VERSION,
        "pathogen": pathogen,
        "pdg_release": pdg_release,
        "written_at": datetime.utcnow().isoformat() + "Z",
        "clusters": {pds: asdict(snap) for pds, snap in keep.items()},
    }
    p = state_path(pathogen, state_dir)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(p)
    log.info("[%s] saved state: %d clusters tracked", pathogen, len(keep))
    return p


def diff(
    pathogen: str,
    prior: dict[str, ClusterSnapshot],
    current_clusters: dict[str, Cluster],
) -> list[Event]:
    """Compare yesterday's snapshot to today's clusters; emit events."""
    events: list[Event] = []

    for pds, cluster in current_clusters.items():
        if cluster.n_human == 0:
            continue  # we only care about human-containing clusters

        prev = prior.get(pds)
        cur_humans = set(h.pdt_acc for h in cluster.humans)
        cur_nonhumans = set(n.pdt_acc for n in cluster.nonhumans)

        if prev is None:
            # Brand-new cluster
            if cluster.n_human >= config.MIN_HUMAN_CASES_NEW_CLUSTER and config.ALERT_NEW_HUMAN_CLUSTER:
                events.append(Event(
                    event_type=EventType.NEW_HUMAN_CLUSTER,
                    pathogen=pathogen,
                    pds_acc=pds,
                    summary=(
                        f"New cluster {pds} with {cluster.n_human} human case(s) "
                        f"and {cluster.n_nonhuman} nonhuman isolate(s)"
                    ),
                    new_pdt_accs=sorted(cur_humans | cur_nonhumans),
                ))
            continue

        prev_humans = set(prev.human_pdt_accs)
        prev_nonhumans = set(prev.nonhuman_pdt_accs)

        added_humans = cur_humans - prev_humans
        added_nonhumans = cur_nonhumans - prev_nonhumans

        if added_humans and config.ALERT_NEW_HUMAN_IN_CLUSTER:
            events.append(Event(
                event_type=EventType.NEW_HUMAN_IN_CLUSTER,
                pathogen=pathogen,
                pds_acc=pds,
                summary=(
                    f"Cluster {pds} gained {len(added_humans)} new human case(s) "
                    f"(now {cluster.n_human} total)"
                ),
                new_pdt_accs=sorted(added_humans),
            ))

        if added_nonhumans and config.ALERT_NONHUMAN_NEAR_HUMAN:
            events.append(Event(
                event_type=EventType.NEW_NONHUMAN_NEAR_HUMAN,
                pathogen=pathogen,
                pds_acc=pds,
                summary=(
                    f"Cluster {pds} (with {cluster.n_human} human case(s)) "
                    f"gained {len(added_nonhumans)} new nonhuman isolate(s)"
                ),
                new_pdt_accs=sorted(added_nonhumans),
            ))

    log.info("[%s] diff produced %d events", pathogen, len(events))
    return events
