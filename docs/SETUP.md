# Setup Guide

How to deploy Pathogen Watch to your GitHub account in ~30 minutes. Cost: $0.

## 1. Create the GitHub repository

```bash
# Unzip this project
unzip pathogen-watch.zip
cd pathogen-watch

# Initialize git and push
git init
git add .
git commit -m "Initial commit"
gh repo create pathogen-watch --public --source=. --remote=origin --push
```

Repo must be **public** for the free GitHub Pages tier and unlimited Actions
minutes.

## 2. Enable GitHub Pages

1. Go to repository **Settings → Pages**.
2. Under "Build and deployment", set **Source** to **GitHub Actions** (not
   "Deploy from a branch").
3. That's it. The workflow will deploy automatically.

The site will be served at `https://YOUR_USERNAME.github.io/pathogen-watch/`.

## 3. Enable GitHub Discussions (for the comment widget)

1. Repository **Settings → General → Features**, check **Discussions**.
2. Go to the **Discussions** tab (now visible), click **New category**:
   - Name: `Cluster discussions`
   - Format: `Announcement` (only maintainers can start threads; visitors can
     still comment — this prevents spam threads. Use `Open-ended discussion`
     if you want anyone to start a thread.)

## 4. Configure Giscus

1. Visit https://giscus.app
2. Enter your repo (`YOUR_USERNAME/pathogen-watch`).
3. Under "Page ↔ Discussions mapping", choose **Discussion title contains
   page <code>data-term</code>**.
4. Under "Discussion category", choose `Cluster discussions`.
5. Scroll to "Enable giscus" — copy the four values:
   - `data-repo` (your repo)
   - `data-repo-id`
   - `data-category`
   - `data-category-id`

## 5. Add repository variables and secrets

In **Settings → Secrets and variables → Actions**:

### Variables (publicly visible in HTML — that's fine)

| Name | Value |
|---|---|
| `SITE_BASE_URL` | `https://YOUR_USERNAME.github.io/pathogen-watch` |
| `GISCUS_REPO` | `YOUR_USERNAME/pathogen-watch` |
| `GISCUS_REPO_ID` | from giscus.app |
| `GISCUS_CATEGORY` | `Cluster discussions` |
| `GISCUS_CATEGORY_ID` | from giscus.app |

### Secrets (private — for the email digest)

| Name | Value |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASS` | a Gmail [app password](https://support.google.com/accounts/answer/185833) (not your regular password!) |
| `ALERT_FROM` | the same Gmail address |
| `ALERT_TO` | comma-separated recipients |

The email part is optional; if any SMTP secret is missing, the workflow
still publishes the site, just without sending emails.

## 6. First run

```bash
# Manually trigger from the Actions tab
# → "Daily pathogen surveillance" → "Run workflow"
```

The first run takes ~5–10 minutes (downloads ~300 MB of NCBI metadata).
**It produces zero alert events** — there's no prior state to diff against.
Site renders fully though, including all current mixed clusters.

The **second run** is the real first surveillance day. After that the cron
takes over: 09:00 UTC daily = 04:00 EST / 05:00 EDT.

## 7. Optional: custom domain

If you want `pathogen-watch.org` instead of the GitHub URL (~$12/year):

1. Register at any registrar (Cloudflare, Porkbun, Namecheap).
2. Repo **Settings → Pages → Custom domain**, enter your domain.
3. At your registrar, add the DNS records GitHub Pages instructs (CNAME
   pointing to `YOUR_USERNAME.github.io`).
4. Update the `SITE_BASE_URL` repo variable to the new domain.

## Troubleshooting

**Workflow fails on first run with permission error.**
Check **Settings → Actions → General → Workflow permissions** — set to
**Read and write permissions**.

**Pages doesn't deploy.**
Confirm **Settings → Pages → Source** is set to **GitHub Actions**, not
"Deploy from a branch". The latter would deploy from `main` and ignore
the workflow's artifact.

**Giscus widget doesn't appear.**
Check the browser console. Most often it's that you haven't enabled
Discussions on the repo, or the category ID is wrong. The cluster page
renders fine without it — comments just won't show.

**Email digest never arrives.**
Gmail may flag the first few; check spam. If using Gmail and an app
password, make sure 2FA is enabled on the account first (Google requires
it before app passwords are available).
