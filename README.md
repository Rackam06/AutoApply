# AutoApply

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Local LLM](https://img.shields.io/badge/LLM-Ollama-black.svg)](https://ollama.com)

**Find company contacts, draft tailored outreach with a local LLM, and send applications — on your machine, under your control.**

AutoApply is a local-first job outreach toolkit: a Streamlit dashboard for review & sending, plus an unattended worker that scrapes leads, proposes search angles from your profile, drafts emails with Ollama, self-reviews them, and queues only the ones that pass.

No SaaS. No cloud LLM required. Your `.env`, your CVs, your `leads.db`.

---

## How it works

```
┌─────────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  Your Profile       │────▶│  outreach_worker.py  │────▶│  leads.db       │
│  (bio, CV, phone)   │     │  plan → scrape →     │     │  ready_to_send  │
└─────────────────────┘     │  draft → self-review │     │  / flagged      │
                            └──────────────────────┘     └────────┬────────┘
                                                                   │
                            ┌──────────────────────┐               │
                            │  streamlit run app.py│◀──────────────┘
                            │  edit · preview · send│
                            └──────────────────────┘
```

1. **Profile** — name, phone, bio, CVs in the dashboard (or `docs/`).
2. **Worker** — Ollama suggests diversified job-search angles from your bio/CV, scrapes company emails, drafts + self-reviews each message.
3. **Dashboard** — review **Ready to Send** drafts (optional edits), send via Gmail when you choose. The worker never sends email.

---

## Features

| Area | What you get |
|---|---|
| **Profile-driven search** | Local LLM proposes 4–6 search angles (not only “Data Science”) from your bio & CV |
| **Smart scraping** | Multi-backend search, job-board / ATS link following, contact-page crawling, domain filters |
| **Email scoring** | 0–100 quality score; jobs/contact prefixes and company domains rank higher |
| **Local drafting** | Ollama drafts subject/body, then critiques and revises its own draft |
| **Quality gates** | Deterministic pre-filter (placeholders, length, name/phone) + LLM self-review |
| **SQLite queue** | Concurrent-safe `leads.db` shared by worker and dashboard |
| **Manual send** | Dry-run previews, delay + jitter, daily send cap, CV attachment by country |
| **CSV export** | Export anytime; CSV is no longer the source of truth |

---

## Requirements

- **Python 3.11+**
- **[Ollama](https://ollama.com)** with a chat model pulled (e.g. `gemma3:4b-it-qat`, `gemma4:e4b`, `ministral-3:3b`)
- **Gmail** with an [App Password](https://support.google.com/accounts/answer/185833) (only needed when *sending*)
- Optional GPU — CPU works; drafts are slower (often 2–5 minutes each on small models)

---

## Quick start

### 1. Clone & install

```bash
git clone https://github.com/Rackam06/AutoApply.git
cd AutoApply

python -m venv venv
# macOS / Linux
source venv/bin/activate
# Windows
# venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` at least for sending and Ollama:

| Variable | Purpose |
|---|---|
| `MY_EMAIL` / `MY_APP_PASSWORD` | Gmail SMTP (sending only) |
| `APPLICANT_*` | Prefill fallbacks for name/phone/website/LinkedIn |
| `CV_FILE_EN` / `CV_FILE_FR` | Default PDF paths under `docs/` |
| `OLLAMA_HOST` | Default `http://localhost:11434` |
| `OLLAMA_MODEL` | e.g. `gemma3:4b-it-qat` |
| `OLLAMA_TIMEOUT` | Read timeout in seconds (use **300+** on CPU) |
| `DAILY_EMAIL_CAP` | Max real sends per day (default `50`) |

> Prefer filling **name, phone, bio, and CVs in the Streamlit profile UI** — that is what the worker uses for planning and drafting. `.env` values are fallbacks.

### 3. Add CVs

```text
docs/
  CV_English.pdf
  CV_French.pdf    # used when lead country is France
```

You can also upload PDFs from the dashboard; they are saved into `docs/`.

### 4. Start Ollama

```bash
ollama serve          # if not already running as a service
ollama pull gemma3:4b-it-qat   # or whichever model you set in .env
```

### 5. Fill your profile (dashboard)

```bash
streamlit run app.py
```

Open the app → **Your Profile**:

- First / last name (exact spelling — small models often garble surnames)
- Phone (appended to the email signature — strongly recommended)
- Website / LinkedIn (optional)
- Bio + English/French CV PDFs  
→ **Save Profile**

### 6. Run the outreach worker

```bash
# Recommended: profile-driven plan + scrape + draft loop
python outreach_worker.py

# Preview search angles only (no scraping)
python outreach_worker.py --plan-only

# Reuse the last plan without calling the LLM again
python outreach_worker.py --reuse-plan

# Manual override (old style)
python outreach_worker.py --field "Data Science" --type "Full-time job" --remote
```

Stop with **Ctrl+C** — you get a run summary. Logs go to stdout and `worker.log`.

### 7. Review & send

Keep (or restart) the dashboard:

1. **Ready to Send** — edit subject/body if you want, select rows  
2. **Flagged** — drafts that failed self-review (check notes)  
3. **Email Operations** — dry-run first, then send  

The worker **never** sends mail. Sending is always a manual click.

---

## Recommended daily workflow

1. Morning: ensure Ollama is up and profile is saved.  
2. `python outreach_worker.py` (or `--reuse-plan` if yesterday’s plan was good).  
3. Leave it running; it rotates search angles across cycles (default 15 min apart).  
4. Open the dashboard when you have time → skim Ready to Send → dry-run → send a small batch.  
5. Ctrl+C the worker when you’re done for the day.

Edit `search_plan.json` by hand anytime, then use `--reuse-plan`.

---

## Project layout

| Path | Role |
|---|---|
| `app.py` | Streamlit dashboard (profile, leads, send) |
| `outreach_worker.py` | CLI worker (plan → scrape → draft → queue) |
| `scraper.py` | Search, domain filters, email extraction & scoring |
| `llm.py` | Ollama client: plan, classify, draft, self-review |
| `db.py` | SQLite (`leads.db`) + profile helpers |
| `cv_utils.py` | CV paths, PDF text extraction for drafting |
| `test_ollama.py` | Diagnose Ollama + run the draft pipeline on one lead |
| `.env.example` | Documented config template |
| `search_plan.json` | Last generated search plan (gitignored) |
| `leads.db` | Lead queue + profile (gitignored) |
| `worker.log` | Rotating worker log (gitignored) |

---

## Worker CLI reference

```text
python outreach_worker.py [options]

  (no --field/--type)     Build a search plan from your profile, then scrape
  --plan-only             Generate/print plan and exit
  --reuse-plan            Use search_plan.json instead of regenerating
  --field / --type        Manual single-angle mode (both required together)
  --location              Location string (manual mode)
  --remote                Add remote bias (manual mode)
  --searches-per-cycle N  Angles per cycle (default 1; rotates through plan)
  --cycle-interval SEC    Sleep between cycles (default 900)
  --min-score N           Skip leads below this score (default 40)
  --max-results N         Search hits per backend query (default 10)
```

---

## Testing Ollama

If drafts time out or look empty:

```bash
# Clear a stuck generation (common after client timeouts on CPU)
curl -s http://localhost:11434/api/generate \
  -d '{"model":"gemma3:4b-it-qat","keep_alive":0}'

python test_ollama.py              # uses a lead from leads.db if present
python test_ollama.py --lead-id 6
python test_ollama.py --ping-only
python test_ollama.py --model gemma4:e4b
```

**Notes for thinking models** (e.g. `gemma4:e4b`): AutoApply sends `think: false` on JSON calls so generation budget is not spent on hidden reasoning.

**CPU tip:** raise `OLLAMA_TIMEOUT` (300–600). Do not lower it and spam retries — timed-out jobs keep running on the server and block the next request.

---

## Dashboard overview

| Section | Purpose |
|---|---|
| **Your Profile** | Name, phone, links, bio, CV uploads → used by planning & drafting |
| **Auto-Scrape** | Optional one-off scrape from the UI (worker is preferred for drafting) |
| **Ready to Send** | Editable drafts queued by the worker |
| **Flagged** | Failed self-review with notes |
| **Email Operations** | Dry-run / send with delay jitter and daily cap |
| **Export CSV** | Snapshot of all leads |

Score guide (scraped contacts):

| Score | Meaning |
|---|---|
| ≥ 60 | Strong — worth prioritizing |
| 40–59 | Worth reviewing (worker default cutoff) |
| < 40 | Speculative / noisy |

---

## Privacy & data

Everything stays local by default:

- `leads.db`, `search_plan.json`, `worker.log`, `.env`, and `docs/` are **gitignored**
- LLM calls go to **your** Ollama host
- Email is sent only through **your** Gmail SMTP when you click Send

Never commit `.env` or CVs with personal data.

---

## Contributing

Issues and pull requests are welcome. See **[CONTRIBUTING.md](CONTRIBUTING.md)** for setup, PR guidelines, and what fits the project.

Please keep personal data (`.env`, CVs, databases, outreach logs) out of PRs.

---

## Disclaimer

AutoApply is for **personal, responsible job outreach**. Respect websites’ terms of service and applicable anti-spam / privacy laws. Prefer genuine, targeted messages over volume. The authors are not responsible for account bans, blocked SMTP, or misuse.

---

## License

Released under the [MIT License](LICENSE). Copyright © 2026 Wail Ameur.
