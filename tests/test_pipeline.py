"""Tests for the cluster/diff logic.

Uses synthetic Isolate objects so tests run offline. Validates:
- mixed-cluster detection
- ≥N-human cluster detection
- near-neighbor SNP thresholds
- state diff producing correct event types
- empty/edge cases
"""

import sys
import tempfile
from datetime import date
from pathlib import Path

# Make src importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import cluster as cluster_mod
from src import state as state_mod
from src.parser import Isolate


def mk(pdt, pds, epi="clinical", min_same=5, min_diff=200, coll=None, source=None, pathogen="Salmonella"):
    return Isolate(
        pdt_acc=pdt, pds_acc=pds, epi_type=epi, host=None,
        isolation_source=source, geo_loc_name="USA: Maryland",
        collection_date=coll or date.today(),
        collection_date_raw=None,
        scientific_name="Salmonella enterica", serovar="Typhimurium",
        min_same=min_same, min_diff=min_diff, amr_genotypes=None,
        biosample_acc=None, asm_acc=None, sra_acc=None, pathogen=pathogen,
    )


def test_group_by_cluster_drops_noncluster():
    isos = [
        mk("PDT001", "PDS_A"),
        mk("PDT002", None),  # no cluster — should be dropped
        mk("PDT003", "PDS_A"),
    ]
    clusters = cluster_mod.group_by_cluster(isos)
    assert "PDS_A" in clusters
    assert clusters["PDS_A"].n_total == 2


def test_mixed_cluster_detection():
    isos = [
        mk("PDT001", "PDS_A", epi="clinical"),
        mk("PDT002", "PDS_A", epi="environmental/other", source="bovine"),
        mk("PDT003", "PDS_B", epi="clinical"),  # human-only cluster
        mk("PDT004", "PDS_B", epi="clinical"),
    ]
    clusters = cluster_mod.group_by_cluster(isos)
    mixed = cluster_mod.find_mixed_clusters(clusters)
    assert len(mixed) == 1
    assert mixed[0].pds_acc == "PDS_A"
    assert mixed[0].n_human == 1
    assert mixed[0].n_nonhuman == 1


def test_human_cluster_threshold():
    isos = [
        mk("PDT001", "PDS_A", epi="clinical"),
        mk("PDT002", "PDS_A", epi="clinical"),
        mk("PDT003", "PDS_A", epi="clinical"),
        mk("PDT004", "PDS_B", epi="clinical"),
    ]
    clusters = cluster_mod.group_by_cluster(isos)
    big = cluster_mod.find_human_clusters(clusters, min_humans=2)
    assert len(big) == 1
    assert big[0].pds_acc == "PDS_A"


def test_tight_nonhuman_neighbors():
    isos = [
        # nonhuman with very tight cross-cluster neighbor (≤5 SNPs)
        mk("PDT001", "PDS_A", epi="environmental/other", min_diff=3, source="poultry"),
        # nonhuman, modest distance (≤50)
        mk("PDT002", "PDS_A", epi="environmental/other", min_diff=42, source="bovine"),
        # nonhuman, too far
        mk("PDT003", "PDS_B", epi="environmental/other", min_diff=200, source="swine"),
        # human — should be ignored by this function
        mk("PDT004", "PDS_A", epi="clinical", min_diff=1),
    ]
    clusters = cluster_mod.group_by_cluster(isos)
    tight5 = cluster_mod.find_tight_nonhuman_neighbors(clusters, 5)
    tight50 = cluster_mod.find_tight_nonhuman_neighbors(clusters, 50)
    assert {n.isolate.pdt_acc for n in tight5} == {"PDT001"}
    assert {n.isolate.pdt_acc for n in tight50} == {"PDT001", "PDT002"}


def test_diff_new_human_cluster():
    # No prior state; new cluster with 2 humans → NEW_HUMAN_CLUSTER event
    isos = [
        mk("PDT001", "PDS_NEW", epi="clinical"),
        mk("PDT002", "PDS_NEW", epi="clinical"),
    ]
    clusters = cluster_mod.group_by_cluster(isos)
    events = state_mod.diff("Salmonella", prior={}, current_clusters=clusters)
    assert len(events) == 1
    assert events[0].event_type == state_mod.EventType.NEW_HUMAN_CLUSTER


def test_diff_human_added():
    # Prior cluster had 1 human; now has 2
    prior = {
        "PDS_X": state_mod.ClusterSnapshot(
            pds_acc="PDS_X", n_human=1, n_nonhuman=0,
            human_pdt_accs=["PDT001"], nonhuman_pdt_accs=[],
            tightest_human_min_same=10, latest_collection="2026-05-01",
        )
    }
    isos = [
        mk("PDT001", "PDS_X", epi="clinical"),
        mk("PDT002", "PDS_X", epi="clinical"),  # new
    ]
    clusters = cluster_mod.group_by_cluster(isos)
    events = state_mod.diff("Salmonella", prior=prior, current_clusters=clusters)
    types = [e.event_type for e in events]
    assert state_mod.EventType.NEW_HUMAN_IN_CLUSTER in types
    # Verify the new isolate is correctly identified
    ev = next(e for e in events if e.event_type == state_mod.EventType.NEW_HUMAN_IN_CLUSTER)
    assert ev.new_pdt_accs == ["PDT002"]


def test_diff_nonhuman_added_to_human_cluster():
    prior = {
        "PDS_Y": state_mod.ClusterSnapshot(
            pds_acc="PDS_Y", n_human=2, n_nonhuman=0,
            human_pdt_accs=["PDT001", "PDT002"], nonhuman_pdt_accs=[],
            tightest_human_min_same=5, latest_collection="2026-05-01",
        )
    }
    isos = [
        mk("PDT001", "PDS_Y", epi="clinical"),
        mk("PDT002", "PDS_Y", epi="clinical"),
        mk("PDT003", "PDS_Y", epi="environmental/other", source="bovine"),  # new
    ]
    clusters = cluster_mod.group_by_cluster(isos)
    events = state_mod.diff("Salmonella", prior=prior, current_clusters=clusters)
    types = [e.event_type for e in events]
    assert state_mod.EventType.NEW_NONHUMAN_NEAR_HUMAN in types


def test_diff_no_event_when_only_one_human_in_new_cluster():
    # New cluster with only 1 human shouldn't fire (threshold is 2)
    isos = [mk("PDT001", "PDS_LONE", epi="clinical")]
    clusters = cluster_mod.group_by_cluster(isos)
    events = state_mod.diff("Salmonella", prior={}, current_clusters=clusters)
    # No NEW_HUMAN_CLUSTER event because n_human < 2
    assert not any(e.event_type == state_mod.EventType.NEW_HUMAN_CLUSTER for e in events)


def test_state_roundtrip():
    """Save then load — values should survive."""
    isos = [
        mk("PDT001", "PDS_RT", epi="clinical"),
        mk("PDT002", "PDS_RT", epi="environmental/other", source="poultry"),
    ]
    clusters = cluster_mod.group_by_cluster(isos)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        state_mod.save_state("TestPath", clusters, "PDG000.1", state_dir=td_path)
        loaded = state_mod.load_state("TestPath", state_dir=td_path)
        assert "PDS_RT" in loaded
        assert loaded["PDS_RT"].n_human == 1
        assert loaded["PDS_RT"].n_nonhuman == 1


def test_isolate_classification():
    h = mk("PDT001", "PDS_A", epi="clinical")
    assert h.is_human and not h.is_nonhuman
    n = mk("PDT002", "PDS_A", epi="environmental/other")
    assert n.is_nonhuman and not n.is_human
    # NULL/empty epi_type means neither
    u = mk("PDT003", "PDS_A", epi=None)
    assert not u.is_human and not u.is_nonhuman


def test_country_extraction():
    iso = mk("PDT001", "PDS_A")
    iso.geo_loc_name = "USA: Maryland, Baltimore"
    assert iso.country() == "USA"
    iso.geo_loc_name = "Germany"
    assert iso.country() == "Germany"
    iso.geo_loc_name = None
    assert iso.country() is None


if __name__ == "__main__":
    # Simple inline runner so you don't need pytest
    import traceback
    funcs = [g for n, g in list(globals().items()) if n.startswith("test_") and callable(g)]
    passed = failed = 0
    for f in funcs:
        try:
            f()
            print(f"  PASS  {f.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {f.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
