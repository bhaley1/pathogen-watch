"""Render a sample digest with synthetic data so we can eyeball the HTML."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import cluster as cluster_mod
from src import digest
from src import state as state_mod
from src.parser import Isolate


def mk(pdt, pds, epi="clinical", min_same=5, min_diff=200, days_ago=10, source=None, geo="USA: Maryland", pathogen="Salmonella"):
    return Isolate(
        pdt_acc=pdt, pds_acc=pds, epi_type=epi, host=None,
        isolation_source=source, geo_loc_name=geo,
        collection_date=date.today() - timedelta(days=days_ago),
        scientific_name="Salmonella enterica", serovar="Typhimurium",
        min_same=min_same, min_diff=min_diff, amr_genotypes=None,
        biosample_acc=None, asm_acc=None, sra_acc=None, pathogen=pathogen,
    )


def sample_pathogen_data(pathogen):
    # Make some clusters
    isos = [
        # Mixed cluster with active human cases
        mk(f"PDT_{pathogen}_001", f"PDS_{pathogen}_A", epi="clinical", min_same=3, days_ago=5),
        mk(f"PDT_{pathogen}_002", f"PDS_{pathogen}_A", epi="clinical", min_same=3, days_ago=7),
        mk(f"PDT_{pathogen}_003", f"PDS_{pathogen}_A", epi="environmental/other",
           source="bovine - milk", min_same=4, days_ago=12),
        mk(f"PDT_{pathogen}_004", f"PDS_{pathogen}_A", epi="environmental/other",
           source="poultry - broiler", min_same=8, days_ago=20),
        # Another mixed cluster, different geography
        mk(f"PDT_{pathogen}_005", f"PDS_{pathogen}_B", epi="clinical",
           geo="USA: California", days_ago=3),
        mk(f"PDT_{pathogen}_006", f"PDS_{pathogen}_B", epi="clinical",
           geo="USA: Oregon", days_ago=4),
        mk(f"PDT_{pathogen}_007", f"PDS_{pathogen}_B", epi="environmental",
           source="lettuce", geo="USA: California", days_ago=8),
        # Tight cross-cluster neighbor
        mk(f"PDT_{pathogen}_008", f"PDS_{pathogen}_C", epi="environmental/other",
           source="swine - cecal", min_diff=3, days_ago=15),
        mk(f"PDT_{pathogen}_009", f"PDS_{pathogen}_D", epi="environmental",
           source="ground beef", min_diff=22, days_ago=18),
    ]
    clusters = cluster_mod.group_by_cluster(isos)
    mixed = cluster_mod.find_mixed_clusters(clusters)
    mixed_recent = cluster_mod.filter_recent_activity(mixed)
    mixed_recent.sort(key=lambda c: (c.latest_collection or date.min), reverse=True)
    neighbors = {
        t: cluster_mod.find_tight_nonhuman_neighbors(clusters, t)
        for t in [5, 10, 50]
    }
    # Fake some events
    events = [
        state_mod.Event(
            event_type=state_mod.EventType.NEW_HUMAN_CLUSTER,
            pathogen=pathogen, pds_acc=f"PDS_{pathogen}_B",
            summary=f"New cluster PDS_{pathogen}_B with 2 human case(s) and 1 nonhuman isolate(s)",
            new_pdt_accs=[f"PDT_{pathogen}_005", f"PDT_{pathogen}_006", f"PDT_{pathogen}_007"],
        ),
        state_mod.Event(
            event_type=state_mod.EventType.NEW_NONHUMAN_NEAR_HUMAN,
            pathogen=pathogen, pds_acc=f"PDS_{pathogen}_A",
            summary=f"Cluster PDS_{pathogen}_A (with 2 human case(s)) gained 1 new nonhuman isolate(s)",
            new_pdt_accs=[f"PDT_{pathogen}_004"],
        ),
    ]
    return events, mixed_recent, neighbors


def main():
    events_by_p = {}
    mixed_by_p = {}
    neighbors_by_p = {}
    for p in ["Salmonella", "STEC", "Listeria", "Campylobacter"]:
        ev, mx, nn = sample_pathogen_data(p)
        events_by_p[p] = ev
        mixed_by_p[p] = mx
        neighbors_by_p[p] = nn

    run_date = date.today()
    html = digest.render_html(run_date, events_by_p, mixed_by_p, neighbors_by_p)
    text = digest.render_text(run_date, events_by_p, mixed_by_p, neighbors_by_p)

    out_dir = Path("/mnt/user-data/outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sample-digest.html").write_text(html)
    (out_dir / "sample-digest.txt").write_text(text)
    print(f"Wrote sample-digest.html ({len(html)} chars)")
    print(f"Wrote sample-digest.txt ({len(text)} chars)")
    print()
    print("--- First 60 lines of text digest ---")
    for line in text.split("\n")[:60]:
        print(line)


if __name__ == "__main__":
    main()
