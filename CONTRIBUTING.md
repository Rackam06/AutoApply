# Contributing to AutoApply

Thanks for taking an interest in improving AutoApply. Small, focused changes are easiest to review.

## Ground rules

- Keep personal data out of the repo: no `.env`, CVs, `leads.db`, or real outreach logs in PRs.
- Prefer one concern per pull request (scraping, LLM, UI, docs, etc.).
- Match the existing style: plain Python, stdlib where it fits, minimal new dependencies.
- Do not add cloud LLM providers or telemetry by default — this project is local-first.

## Development setup

```bash
git clone https://github.com/Rackam06/AutoApply.git
cd AutoApply
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

You need a running [Ollama](https://ollama.com) instance and a pulled model to exercise drafting.

Useful checks:

```bash
python -m py_compile app.py scraper.py db.py llm.py outreach_worker.py cv_utils.py
python test_ollama.py --ping-only
python outreach_worker.py --plan-only
```

## Pull requests

1. Fork and create a branch from `main`.
2. Describe **what** changed and **why**.
3. Link any related issue.
4. If you change behaviour (CLI flags, DB schema, env vars), update `README.md` and `.env.example`.

## Ideas that fit well

- Better deduping / lead quality heuristics
- More resilient Ollama JSON parsing
- Dashboard UX for search plans
- Tests around `scraper` scoring and `llm.pre_filter_draft`
- Docs / translations of the README

## Ideas that need discussion first

- Switching the default storage backend
- Non-Gmail SMTP providers (welcome, but design the config carefully)
- Automated sending without a human click (out of scope by design)

## Security

If you find a vulnerability (e.g. path traversal on CV uploads, credential leakage), please open a private report to the maintainer rather than filing a public issue with exploit details.
