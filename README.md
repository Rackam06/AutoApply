# AutoApply 🚀

A Streamlit web app that **finds real company emails**, **scores them**, and lets you **send personalised job applications** in bulk.

Originally built for Data Science & FinTech internship hunting — but works for any job search. Just change the email templates in `.env`.

---

## Features

| Feature | Details |
|---|---|
| **Smart scraping** | DuckDuckGo search + enriched queries, media/listicle detection, sub-page crawling (contact, about, careers, impressum…) |
| **Email scoring (0–100)** | Company-domain emails score higher; jobs/careers prefixes score highest; generic free-email (gmail, hotmail…) are penalised |
| **Email type classification** | `jobs`, `contact`, `person`, `info`, `generic` |
| **Country detection** | Inferred from TLD (`.fr` → France, `.de` → Germany, 20+ countries) |
| **Editable lead table** | Edit company names, filter by score, select rows for sending |
| **Batch email sending** | Gmail SMTP with CV attachment; dry-run mode to preview before sending |
| **CSV export** | One-click export of your lead list |

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/yourname/AutoApply.git
cd AutoApply
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your Gmail credentials, name, and CV paths
```

> **Gmail App Password** – you need a [Google App Password](https://support.google.com/accounts/answer/185833), not your regular Gmail password.

### 3. Add your CV(s)

Create a `docs/` folder and drop your PDF(s) inside:

```
docs/
  CV_English.pdf
  CV_French.pdf   (optional)
```

### 4. Run

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Usage Guide

### Scraping
1. Enter a search query in the **Auto-Scrape** panel.
   - Be specific: `"AI startup Berlin hiring intern"` works much better than `"AI startup"`
   - Adding a city or country targets local companies
2. Set **Max URLs** (10–20 is usually enough to start)
3. Click **Start Scraping** – the bot will:
   - Run enriched DuckDuckGo queries
   - Skip job boards and social networks
   - Detect media/listicle pages and follow their company links instead
   - Crawl contact/careers sub-pages when no email is on the homepage
4. Results appear in the **Manage Leads** table with scores

### Understanding Scores
| Score | Meaning |
|---|---|
| ≥ 70 | Excellent – recruitment or contact email at company domain |
| 50–69 | Good – likely useful contact address |
| 30–49 | Speculative – generic address, worth a try |
| < 30 | Weak – may be a free-email or noisy address |

### Sending Emails
1. Use **Select High-Score (≥60)** to auto-select the best leads
2. Make sure **Dry run** is checked first to preview your emails
3. Once happy, uncheck Dry run and click **Send**
4. A rate-limit delay between sends is applied automatically

---

## Customising Templates

Edit `EMAIL_SUBJECT_FR/EN` and `EMAIL_BODY_FR/EN` in your `.env` file.  
Available placeholders: `{company_name}`, `{signature}`.

---

## Sharing with Others

1. Fork / copy the repo
2. Each person creates their own `.env` from `.env.example`
3. Everyone puts their own CV in `docs/`
4. `leads.csv` is gitignored — each user keeps their own leads locally

---

## Requirements

- Python 3.11+
- A Gmail account with [App Passwords](https://support.google.com/accounts/answer/185833) enabled

---

## Legal Note

Web scraping publicly available contact information is generally legal, but always respect `robots.txt` and a site's terms of service. This tool is intended for personal job-application use only.
