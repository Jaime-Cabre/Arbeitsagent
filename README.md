# Job board: daily agent + GitHub Pages page

The agent (`job_agent.py`) runs every day on GitHub, scores each posting, and writes `docs/jobs.json`.
The page in `docs/` reads that file, so it stays current without you running anything.

## One-time setup (about 15 minutes)

1. **Create a GitHub repository** and upload everything in this folder (keep the `docs` and `.github` folders as they are).
2. **Get free Adzuna keys** at developer.adzuna.com. This is the main job source, so don't skip it.
3. In the repository: **Settings > Secrets and variables > Actions > New repository secret**. Add `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`.
4. **Settings > Pages**: set the source to "Deploy from a branch", branch `main`, folder `/docs`. Your board will be at `https://<your-username>.github.io/<repo-name>/`.
5. **Actions tab > Refresh jobs > Run workflow** to fill the board the first time. After that it refreshes daily.

## Things to know

- **Free GitHub Pages sites are public.** Anyone with the link can see the board, your resume keywords (`resume.txt`) and your hidden-company list. `resume.txt` has no name, email or phone. Use a private repository only if you have a GitHub plan that supports private Pages.
- **Company boards:** `config.json` has empty `greenhouse`, `lever` and `ashby` lists. Add companies as `"board-name": "Company Name"`, for example `"greenhouse": {"acme": "Acme Inc"}`. Postings from these boards show real posting dates.
- **Scoring:** change weights, `size_preference` (`small` or `large`), industry scores, search terms, `max_age_days` and `exclude_companies` in `config.json`.
- **Company sizes:** `companies.csv` holds approximate employee counts (or `approx_revenue_usd_m` when headcount is unknown). Unknown companies get a neutral size score.
- **Resume:** replace `resume.txt` when your resume changes. It is only used for the keyword match.
- **Run it on your computer instead:** `python job_agent.py` writes the same files to `output/` and `docs/jobs.json`; `python job_agent.py --sample` runs on made-up postings.
- GitHub may pause scheduled workflows in a repository with no activity for 60 days. If the board stops updating, run the workflow once by hand.
