"""Render a sample site with synthetic data so we can preview it."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import cluster as cluster_mod
from src import state as state_mod
from src.parser import Isolate
from src.site import generator as gen


def mk(pdt, pds, epi="clinical", min_same=5, min_diff=200, days_ago=10,
       source=None, geo="USA: Maryland", pathogen="Salmonella", serovar="Typhimurium"):
    return Isolate(
        pdt_acc=pdt, pds_acc=pds, epi_type=epi, host=None,
        isolation_source=source, geo_loc_name=geo,
        collection_date=date.today() - timedelta(days=days_ago),
        collection_date_raw=None,
        scientific_name=f"{pathogen} sp.", serovar=serovar,
        min_same=min_same, min_diff=min_diff, amr_genotypes=None,
        biosample_acc=f"SAMN0{abs(hash(pdt)) % 9999999:07d}", asm_acc=None, sra_acc=None,
        pathogen=pathogen,
    )


def build_synthetic():
    isos = {
        "Salmonella": [
            mk("PDT0001.1", "PDS000123456.7", epi="clinical", min_same=3, days_ago=5, geo="USA: Maryland"),
            mk("PDT0002.1", "PDS000123456.7", epi="clinical", min_same=3, days_ago=7, geo="USA: Virginia"),
            mk("PDT0003.1", "PDS000123456.7", epi="clinical", min_same=4, days_ago=2, geo="USA: New York"),
            mk("PDT0004.1", "PDS000123456.7", epi="environmental/other", source="bovine - milk", min_same=4, days_ago=12, geo="USA: Wisconsin"),
            mk("PDT0005.1", "PDS000123456.7", epi="environmental/other", source="poultry - broiler", min_same=8, days_ago=20, geo="USA: Georgia"),
            mk("PDT0010.1", "PDS000222001.3", epi="clinical", min_same=5, days_ago=3, geo="USA: California", serovar="Enteritidis"),
            mk("PDT0011.1", "PDS000222001.3", epi="clinical", min_same=5, days_ago=4, geo="USA: Oregon", serovar="Enteritidis"),
            mk("PDT0012.1", "PDS000222001.3", epi="environmental", source="lettuce", min_same=8, days_ago=8, geo="USA: California", serovar="Enteritidis"),
            mk("PDT0020.1", "PDS000333001.1", epi="environmental/other", source="swine - cecal contents", min_diff=3, days_ago=15, pathogen="Salmonella"),
        ],
        "STEC": [
            mk("PDT_E_001.1", "PDS_E_001.1", epi="clinical", min_same=2, days_ago=3, pathogen="STEC", serovar="O157:H7"),
            mk("PDT_E_002.1", "PDS_E_001.1", epi="clinical", min_same=2, days_ago=5, pathogen="STEC", serovar="O157:H7"),
            mk("PDT_E_003.1", "PDS_E_001.1", epi="environmental/other", source="ground beef", min_same=3, days_ago=10, pathogen="STEC", serovar="O157:H7"),
        ],
        "Listeria": [
            mk("PDT_L_001.1", "PDS_L_001.1", epi="clinical", min_same=1, days_ago=4, pathogen="Listeria", serovar=None),
            mk("PDT_L_002.1", "PDS_L_001.1", epi="clinical", min_same=1, days_ago=6, pathogen="Listeria", serovar=None),
            mk("PDT_L_003.1", "PDS_L_001.1", epi="environmental", source="deli meat - turkey", min_same=2, days_ago=9, pathogen="Listeria", serovar=None),
            mk("PDT_L_004.1", "PDS_L_001.1", epi="environmental", source="cheese - soft", min_same=3, days_ago=14, pathogen="Listeria", serovar=None),
        ],
        "Campylobacter": [
            mk("PDT_C_001.1", "PDS_C_001.1", epi="clinical", min_same=6, days_ago=8, pathogen="Campylobacter", serovar=None),
            mk("PDT_C_002.1", "PDS_C_001.1", epi="environmental/other", source="chicken - retail", min_same=6, days_ago=15, pathogen="Campylobacter", serovar=None),
        ],
    }

    all_clusters_by_p = {}
    mixed_by_p = {}
    neighbors_by_p = {}
    events_by_p = {}

    for pathogen, iso_list in isos.items():
        clusters = cluster_mod.group_by_cluster(iso_list)
        all_clusters_by_p[pathogen] = clusters
        mixed = cluster_mod.find_mixed_clusters(clusters)
        mixed = cluster_mod.filter_recent_activity(mixed)
        mixed.sort(key=lambda c: (c.latest_collection or date.min), reverse=True)
        mixed_by_p[pathogen] = mixed
        neighbors_by_p[pathogen] = {
            t: cluster_mod.find_tight_nonhuman_neighbors(clusters, t)
            for t in [5, 10, 50]
        }
        # Fake some events for the dashboard
        if pathogen == "Salmonella":
            events_by_p[pathogen] = [
                state_mod.Event(
                    event_type=state_mod.EventType.NEW_NONHUMAN_NEAR_HUMAN,
                    pathogen=pathogen, pds_acc="PDS000123456.7",
                    summary="Cluster PDS000123456.7 (with 3 human case(s)) gained 1 new nonhuman isolate(s)",
                    new_pdt_accs=["PDT0004.1"],
                ),
                state_mod.Event(
                    event_type=state_mod.EventType.NEW_HUMAN_CLUSTER,
                    pathogen=pathogen, pds_acc="PDS000222001.3",
                    summary="New cluster PDS000222001.3 with 2 human case(s) and 1 nonhuman isolate(s)",
                    new_pdt_accs=["PDT0010.1", "PDT0011.1", "PDT0012.1"],
                ),
            ]
        elif pathogen == "STEC":
            events_by_p[pathogen] = [
                state_mod.Event(
                    event_type=state_mod.EventType.NEW_HUMAN_IN_CLUSTER,
                    pathogen=pathogen, pds_acc="PDS_E_001.1",
                    summary="Cluster PDS_E_001.1 gained 1 new human case(s) (now 2 total)",
                    new_pdt_accs=["PDT_E_002.1"],
                ),
            ]
        else:
            events_by_p[pathogen] = []

    return events_by_p, mixed_by_p, neighbors_by_p, all_clusters_by_p


def main():
    events_by_p, mixed_by_p, neighbors_by_p, all_clusters_by_p = build_synthetic()
    pdg_releases = {p: "PDG000000002.5210" for p in events_by_p}

    gen.render_site(
        run_date=date.today(),
        events_by_pathogen=events_by_p,
        mixed_clusters_by_pathogen=mixed_by_p,
        near_neighbors_by_pathogen=neighbors_by_p,
        all_clusters_by_pathogen=all_clusters_by_p,
        pdg_releases=pdg_releases,
    )
    print("Site rendered.")


if __name__ == "__main__":
    main()
