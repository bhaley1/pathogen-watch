# Pathogen Watch

A daily, open-access surveillance site for human–animal–environmental SNP
clusters in *Salmonella*, STEC, *Listeria*, and *Campylobacter*, built from
the NCBI Pathogen Detection nightly snapshot.

Updates every morning at **04:00 EST / 05:00 EDT** via GitHub Actions.
Hosting, builds, comments, RSS, and data downloads are all free.

→ **Setup guide:** [docs/SETUP.md](docs/SETUP.md)

## What it does

Each morning the pipeline:

1. Fetches the latest NCBI Pathogen Detection metadata snapshot for each of
   the four taxgroups.
2. Groups isolates into PDS clusters and classifies them as
   human-only, mixed, or nonhuman-only.
3. Diffs against yesterday's state to detect three event types:
   - **New human cluster** (≥2 human isolates first appears)
   - **Human added to existing cluster**
   - **Nonhuman added to human cluster**
4. Flags nonhuman isolates whose nearest cross-cluster neighbor is within
   ≤5, ≤10, or ≤50 SNPs (early-warning candidates).
5. Renders a static surveillance site: dashboard, per-cluster pages,
   rolling timeline, JSON/CSV downloads, RSS feed.
6. Sends an email digest to subscribers.
7. Commits the day's state and digests to git so the entire history is
   reproducible.

## Site

| Page | What it shows |
|---|---|
| **Dashboard** (`/`) | New events today, active mixed clusters, tight near-neighbors. Filterable. |
| **Cluster pages** (`/clusters/<slug>.html`) | One per human-containing cluster. Isolate tables, change history, comments. |
| **Timeline** (`/timeline.html`) | Every alert event since launch, grouped by day. |
| **Methods** (`/methods.html`) | Data source, alert triggers, caveats. |
| **Data & API** (`/data.html`) | JSON + CSV downloads, schemas, citation. |

## Data exports (regenerated daily)

- `data/clusters.json` & `.csv` — active human-containing clusters
- `data/events.json` & `.csv` — today's alert events
- `data/near_neighbors.json` & `.csv` — tight cross-cluster nonhumans
- `feed.xml` — RSS 2.0 feed of recent alerts

All released under CC0 / public domain.

## Architecture

```
NCBI nightly Pathogen Detection metadata snapshot (per taxgroup)
        │
        ▼
src/fetcher.py     ── downloads with retry/cache/fallback
        │
        ▼
src/parser.py      ── TSV → typed Isolate records (handles schema drift)
        │
        ▼
src/cluster.py     ── PDS grouping, mixed-cluster detection,
                      tight-neighbor identification via min_same/min_diff
        │
        ▼
src/state.py       ── load prior JSON state, diff, emit Event objects
        │
        ├── src/digest.py    ── email body (HTML + text)
        │
        └── src/site/        ── Jinja2 templates → static HTML + JSON/CSV + RSS
                                deployed to GitHub Pages via Actions
```

## Comments

Per-cluster discussions are powered by **Giscus**, which backs each cluster
page with a thread in GitHub Discussions. Visitors authenticate with their
GitHub account; no separate database, no spam-moderation burden. See
[docs/SETUP.md](docs/SETUP.md) for the four-minute setup.

## Cost

Free. The only optional spend is a custom domain (~$12/year).

| Component | Cost |
|---|---|
| GitHub repo (public) | $0 |
| Actions minutes (public repo) | unlimited, $0 |
| Pages hosting | $0 (100 GB/mo bandwidth) |
| Giscus + Discussions | $0 |
| Gmail SMTP | $0 |

## Development

```bash
pip install -r requirements.txt

python -m src.main --no-email --no-state  # smoke run, no email, no state changes
python tests/test_pipeline.py              # unit tests (offline, synthetic)
python tests/smoke_site.py                 # render a sample site from synthetic data
```

To preview the site locally:

```bash
cd site && python -m http.server 8000
# → http://localhost:8000
```

## Files

```
pathogen-watch/
├── src/
│   ├── config.py            # core pipeline config
│   ├── fetcher.py           # NCBI download
│   ├── parser.py            # TSV → Isolate
│   ├── cluster.py           # PDS grouping
│   ├── state.py             # JSON snapshots, day-over-day diff
│   ├── digest.py            # email rendering
│   ├── email_send.py        # SMTP
│   ├── main.py              # CLI / orchestrator
│   └── site/
│       ├── config.py        # site config (URL, Giscus IDs)
│       └── generator.py     # static-site renderer
├── templates/
│   ├── base.html            # masthead, nav, footer
│   ├── dashboard.html       # /
│   ├── cluster.html         # /clusters/<slug>.html
│   ├── timeline.html        # /timeline.html
│   ├── methods.html         # /methods.html
│   ├── data.html            # /data.html
│   └── about.html           # /about.html
├── site/                    # generated; committed for Pages
│   ├── assets/css/main.css
│   ├── assets/js/dashboard.js
│   ├── data/                # JSON + CSV exports
│   ├── clusters/            # per-cluster pages
│   ├── index.html, timeline.html, ...
│   └── feed.xml
├── state/                   # per-pathogen JSON snapshots + history.jsonl (tracked)
├── digests/                 # email digests, audit trail (tracked)
├── cache/                   # raw NCBI downloads (gitignored)
├── tests/
│   ├── test_pipeline.py
│   ├── smoke_digest.py
│   └── smoke_site.py
├── docs/SETUP.md
└── .github/workflows/daily.yml
```

## Caveats

- NCBI is the source of truth. This is a visualization and diffing layer over
  their data; the clustering and SNP calls are theirs.
- `min_same` is an upper bound on the closest human↔nonhuman distance within
  a cluster, not the exact distance. For exact pairwise SNPs, follow the link
  to NCBI on any cluster page.
- STEC filtering uses AMRFinderPlus `stx` hits — stx-negative pathogenic *E.
  coli* won't appear.
- Daylight saving shifts the 09:00 UTC build by an hour twice a year. Not
  worth the dual-cron complexity unless you really need exact 4 AM local.

## License

CC0 / public domain. Use freely. Attribution appreciated but not required.

## Author

Bradd Haley, PhD — Lead Research Microbiologist, USDA Agricultural Research
Service. Personal project; views are the author's, not the USDA's.
