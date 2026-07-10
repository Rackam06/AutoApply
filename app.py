"""
AutoApply — Smart Lead Finder & Email Bot
Scrape company emails from the web, score them, and send personalised applications.
"""

import json
import os
import re
import smtplib
import time
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from ddgs import DDGS
from dotenv import load_dotenv

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
load_dotenv()

MY_EMAIL           = os.getenv("MY_EMAIL")
MY_APP_PASSWORD    = os.getenv("MY_APP_PASSWORD")
APPLICANT_NAME     = os.getenv("APPLICANT_NAME", "Jane Doe")
APPLICANT_PHONE    = os.getenv("APPLICANT_PHONE", "+1 234 567 890")
APPLICANT_WEBSITE  = os.getenv("APPLICANT_WEBSITE", "www.janedoe.com")
APPLICANT_LINKEDIN = os.getenv("APPLICANT_LINKEDIN", "")

CV_FILE_FR = os.getenv("CV_FILE_FR", "docs/CV_French.pdf")
CV_FILE_EN = os.getenv("CV_FILE_EN", "docs/CV_English.pdf")

EMAIL_SUBJECT_FR = os.getenv("EMAIL_SUBJECT_FR", "Candidature Spontanée – {company_name}")
EMAIL_BODY_FR    = os.getenv(
    "EMAIL_BODY_FR",
    "Bonjour,\n\nJe vous contacte afin de soumettre ma candidature spontanée chez {company_name}.\n\n"
    "Cordialement,\n{signature}",
).replace("\\n", "\n")
EMAIL_SUBJECT_EN = os.getenv("EMAIL_SUBJECT_EN", "Internship Application – {company_name}")
EMAIL_BODY_EN    = os.getenv(
    "EMAIL_BODY_EN",
    "Dear Hiring Manager,\n\nI would like to apply for an internship at {company_name}.\n\n"
    "Best regards,\n{signature}",
).replace("\\n", "\n")

CSV_FILE = "leads.csv"
COLUMNS  = ["Select", "Company", "Website", "Email", "Email Type", "Score", "Country", "Status"]

# ─── DOMAIN / KEYWORD LISTS ───────────────────────────────────────────────────

SOCIAL_DOMAINS = {
    "linkedin.com", "youtube.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "tiktok.com", "pinterest.com", "reddit.com",
    "quora.com", "snapchat.com",
}

# Job boards / aggregators — we never scrape emails FROM these directly, but
# their listing pages link out to the real company sites, so we crawl them
# for outbound links instead of skipping them outright.
JOB_BOARD_DOMAINS = {
    "indeed.com", "glassdoor.com", "welcometothejungle.com",
    "monster.com", "ziprecruiter.com", "simplyhired.com", "careerbuilder.com",
    "jooble.org", "talent.com", "remote.co", "weworkremotely.com",
    "themuse.com", "jobteaser.com", "hellowork.com", "apec.fr",
    "wttj.co", "remoteok.com", "jobicy.com", "workingnomads.com",
    "otta.com", "himalayas.app",
}

# ATS platforms that host a REAL company's job page on a company subdomain
# (e.g. acme.greenhouse.io). These are legitimate company pages, not
# aggregators — we scrape them directly and derive the company name from
# the subdomain rather than treating "greenhouse.io" as the company.
ATS_DOMAINS = {
    "greenhouse.io", "lever.co", "workable.com", "breezy.hr",
    "smartrecruiters.com", "ashbyhq.com", "bamboohr.com",
    "teamtailor.com", "recruitee.com", "personio.com", "jobvite.com",
    "myworkdayjobs.com",
}

# Kept for backwards compatibility with anything that still expects a single
# "never touch" set — social media only, since job boards are now crawled.
SKIP_DOMAINS = SOCIAL_DOMAINS

# Platforms / directories — never scrape; emails here are platform support, not companies
AGGREGATOR_DOMAINS = {
    "f6s.com", "angel.co", "angellist.com", "crunchbase.com", "pitchbook.com",
    "dealroom.co", "seedtable.com", "startupranking.com", "tracxn.com",
    "wellfound.com", "producthunt.com", "betalist.com", "startupstash.com",
    "launchingnext.com", "startupblink.com", "failory.com", "startupgenome.com",
    "sifted.eu", "techstars.com", "ycombinator.com", "500.co",
    "startupcityindia.com", "123articleonline.com", "exploratory.io",
}

MEDIA_DOMAINS = {
    "medium.com", "forbes.com", "techcrunch.com", "venturebeat.com",
    "analyticsindiamag.com", "analyticsinsight.net", "fortune.com",
    "businessinsider.com", "analyticsvidhya.com", "builtin.com",
    "clutch.co", "goodfirms.co", "capterra.com", "g2.com",
    "hackernoon.com", "dev.to", "substack.com", "wired.com",
    "theregister.com", "zdnet.com", "infoq.com", "topmba.com",
    "techradar.com", "techinasia.com", "eu-startups.com", "frenchweb.fr",
    "techvidvan.com", "thedatagrab.com", "planetbesttech.com", "dynamicyield.com",
    "startupmagazine", "startupcity", "articleonline", "blogspot.com",
    "wordpress.com", "wixsite.com", "hubspot.com",
}

NOISE_LINK_DOMAINS = {
    "linkedin", "twitter", "facebook", "instagram", "youtube", "google",
    "apple", "microsoft", "medium", "wikipedia", "amazon", "cloudflare",
    "whatsapp", "telegram", "tiktok", "x.com", "snapchat", "pinterest",
}

JUNK_EMAIL_DOMAINS = {
    "example.com", "w3.org", "sentry.io", "google.com", "linkedin.com",
    "twitter.com", "facebook.com", "instagram.com", "youtube.com",
    "github.com", "wordpress.org", "cloudflare.com", "medium.com",
    "schema.org", "bit.ly", "mailchimp.com", "gravatar.com",
    "amazonaws.com", "wp-rocket.me", "wixpress.com", "squarespace.com",
    "godaddy.com", "namecheap.com", "ovh.com",
}

GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "yahoo.com", "yahoo.fr", "yahoo.co.uk",
    "outlook.com", "live.com", "live.fr", "icloud.com", "msn.com",
    "protonmail.com", "aol.com", "zoho.com", "mail.com", "gmx.com", "gmx.fr",
    "free.fr", "orange.fr", "wanadoo.fr", "laposte.net", "sfr.fr",
    "bbox.fr", "numericable.fr",
}

JUNK_EMAIL_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "admin",
    "hostmaster", "postmaster", "privacy", "webmaster", "mailer-daemon",
    "bounce", "abuse", "spam", "newsletter", "unsubscribe", "marketing",
    "press", "media", "advertis", "legal", "security@",
}

JOBS_PREFIXES = {
    "jobs", "careers", "recrutement", "recruitment", "hiring", "apply",
    "stage", "intern", "candidature", "rh", "hr", "talent",
}
CONTACT_PREFIXES = {
    "contact", "hello", "bonjour", "hi", "hola", "ciao",
    "people", "work", "join", "team", "equipe",
}
MEDIUM_PREFIXES = {
    "info", "support", "help",
}

CONTACT_PAGE_KEYWORDS = [
    "contact", "about", "team", "nous-contacter", "a-propos", "equipe",
    "jobs", "careers", "recrutement", "join", "hiring", "impressum",
    "about-us", "work-with-us", "join-us", "legal-notice",
]

LISTICLE_KEYWORDS = [
    "top ", "best ", " list ", "startups in", "companies in", " guide",
    "roundup", "ranking", "review", " directory", " blog", " news",
    "who to watch", "to watch in", "article", "magazine", "tutorial",
]

# Page titles / names that are bot challenges or placeholders — never use as company names
BAD_NAME_PATTERNS = [
    r"checking your browser",
    r"just a moment",
    r"please wait",
    r"access denied",
    r"403 forbidden",
    r"404 not found",
    r"page not found",
    r"cloudflare",
    r"captcha",
    r"verify you are human",
    r"robot or human",
    r"enable javascript",
    r"security check",
    r"attention required",
    r"one more step",
    r"ddos protection",
    r"under maintenance",
    r"coming soon",
    r"untitled",
    r"default page",
    r"test page",
    r"^home$",
    r"^index$",
    r"^welcome$",
]

BAD_NAME_EXACT = {
    "home", "index", "welcome", "page", "en", "de", "fr", "uk", "us",
    "web", "site", "unknown", "wordpress", "accueil", "homepage", "website",
    "blog", "news", "about", "contact", "loading", "redirect", "error",
    "startup", "company", "business", "untitled", "document",
}

# Application type → search terms (EN + FR)
APP_TYPE_TERMS = {
    "Internship": ["internship", "intern", "stage", "alternance"],
    "Full-time job": ["hiring", "careers", "jobs", "recrutement", "emploi"],
    "Spontaneous application": ["careers", "contact", "join us", "recrutement"],
}

# Common tech-field phrasing variants. Search engines treat "fullstack",
# "full stack" and "full-stack" as different tokens, so a query built from
# only what the user typed misses most real postings. This expands a field
# into the alternate spellings people actually use in job ads.
FIELD_SYNONYMS = {
    "fullstack": ["full stack", "full-stack"],
    "full stack": ["fullstack", "full-stack"],
    "full-stack": ["fullstack", "full stack"],
    "backend": ["back-end", "back end"],
    "back-end": ["backend", "back end"],
    "frontend": ["front-end", "front end"],
    "front-end": ["frontend", "front end"],
    "devops": ["dev ops"],
    "datascience": ["data science"],
    "data science": ["data scientist"],
    "machine learning": ["ml engineer", "ai engineer"],
    "ai": ["artificial intelligence"],
    "fintech": ["financial technology"],
    "ux": ["ui/ux", "user experience"],
    "ui": ["ui/ux", "user interface"],
}

# Job boards worth targeting directly via site: search — we now know how to
# crawl their listing pages for outbound company links.
SITE_SEARCH_BOARDS = ["indeed.com", "welcometothejungle.com", "weworkremotely.com"]
# ATS platforms — a hit here is a real company career page.
SITE_SEARCH_ATS = ["greenhouse.io", "lever.co"]

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TLD_COUNTRY_MAP = {
    ".fr": "France", ".de": "Germany", ".co.uk": "United Kingdom", ".uk": "United Kingdom",
    ".es": "Spain", ".it": "Italy", ".nl": "Netherlands", ".be": "Belgium",
    ".ch": "Switzerland", ".at": "Austria", ".ca": "Canada", ".au": "Australia",
    ".in": "India", ".br": "Brazil", ".mx": "Mexico", ".jp": "Japan",
    ".sg": "Singapore", ".pt": "Portugal", ".pl": "Poland", ".se": "Sweden",
    ".no": "Norway", ".dk": "Denmark", ".fi": "Finland",
}

# ─── DATA LOADING ─────────────────────────────────────────────────────────────

if "leads" not in st.session_state:
    if os.path.exists(CSV_FILE):
        df = pd.read_csv(CSV_FILE)
        # Add any missing columns
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = False if col == "Select" else ("Pending" if col == "Status" else "")
        # Ensure numeric types survive the CSV round-trip
        df["Score"] = pd.to_numeric(df["Score"], errors="coerce").fillna(0).astype(int)
        st.session_state.leads = df
    else:
        st.session_state.leads = pd.DataFrame(columns=COLUMNS)

# Ensure Select is boolean
st.session_state.leads["Select"] = (
    st.session_state.leads["Select"].fillna(False).astype(bool)
    if "Select" in st.session_state.leads.columns
    else False
)


def save_leads() -> None:
    if isinstance(st.session_state.leads, pd.DataFrame):
        st.session_state.leads.to_csv(CSV_FILE, index=False)


def normalize_domain(url_or_domain: str) -> str:
    """Return bare domain without a leading 'www.'."""
    if "://" in url_or_domain:
        url_or_domain = urlparse(url_or_domain).netloc
    domain = url_or_domain.lower()
    # NOTE: this used to be `.lstrip("www.")`, which strips any leading
    # characters found in the set {w, w, w, .} rather than the literal
    # prefix "www." — it was silently mangling any domain that happened to
    # start with a "w" (e.g. "weworkremotely.com" -> "eworkremotely.com",
    # "workday.com" -> "orkday.com", "wise.com" -> "ise.com"), which broke
    # domain matching AND corrupted the "Website" column for those leads.
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def is_hard_blocked(url_or_domain: str) -> bool:
    """Never worth visiting at all — social media & pure startup directories."""
    domain = normalize_domain(url_or_domain)
    blocked = SOCIAL_DOMAINS | AGGREGATOR_DOMAINS
    return any(d in domain for d in blocked)


def is_link_extraction_source(url_or_domain: str) -> bool:
    """
    Job boards & media pages: we don't scrape emails from these directly
    (that would just be the platform's own support address), but their
    listing/article pages link OUT to the real company sites — so we fetch
    them and follow those outbound links instead of skipping them.
    """
    domain = normalize_domain(url_or_domain)
    sources = JOB_BOARD_DOMAINS | MEDIA_DOMAINS
    return any(d in domain for d in sources)


def is_blocked_domain(url_or_domain: str) -> bool:
    """True if this URL should never be scraped AS IF it were a company page."""
    return is_hard_blocked(url_or_domain) or is_link_extraction_source(url_or_domain)


def is_bad_company_name(name: str) -> bool:
    """Reject bot-challenge titles, generic words, and nonsense names."""
    if not name or not name.strip():
        return True
    cleaned = name.strip()
    if len(cleaned) < 2 or len(cleaned) > 60:
        return True
    lower = cleaned.lower()
    if lower in BAD_NAME_EXACT:
        return True
    if any(re.search(p, lower) for p in BAD_NAME_PATTERNS):
        return True
    # Too many words often means a page title, not a company name
    if len(cleaned.split()) > 6:
        return True
    return False


def is_bot_challenge_page(soup: BeautifulSoup) -> bool:
    """Detect Cloudflare / anti-bot interstitial pages."""
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    if is_bad_company_name(title):
        return True
    text_sample = soup.get_text(" ", strip=True)[:2000].lower()
    bot_phrases = [
        "checking your browser", "just a moment", "please enable javascript",
        "verify you are human", "cloudflare", "ddos protection", "captcha",
        "security check", "ray id",
    ]
    return any(p in text_sample for p in bot_phrases)


def email_matches_site(email: str, site_url: str) -> bool:
    """Email domain must match the scraped website — rejects platform/support emails."""
    if "@" not in email:
        return False
    email_domain = normalize_domain(email.rsplit("@", 1)[1])
    site_domain = normalize_domain(site_url)

    if email_domain in AGGREGATOR_DOMAINS or any(d in email_domain for d in AGGREGATOR_DOMAINS):
        return False

    if email_domain == site_domain:
        return True
    if site_domain.endswith("." + email_domain) or email_domain.endswith("." + site_domain):
        return True
    # Same registrable domain (e.g. acme.com vs careers.acme.com)
    def root(d: str) -> str:
        parts = d.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else d

    return root(email_domain) == root(site_domain)


def domain_to_company_name(domain: str) -> str:
    """Turn 'data-recrutement.fr' → 'Data Recrutement'."""
    root = normalize_domain(domain).split(".")[0]
    return re.sub(r"[-_]", " ", root).title()


def _field_variants(field: str) -> List[str]:
    """Return the field as typed plus any known phrasing alternates
    (fullstack / full stack / full-stack, etc.), deduplicated, original first."""
    field = field.strip()
    variants = [field]
    key = field.lower()
    for alt in FIELD_SYNONYMS.get(key, []):
        if alt.lower() not in (v.lower() for v in variants):
            variants.append(alt)
    return variants


def _phrase(term: str) -> str:
    """Quote multi-word terms to keep them adjacent; leave single words
    unquoted since quoting a lone token gains nothing and can make some
    engines stricter about capitalization/stemming."""
    return f'"{term}"' if " " in term.strip() else term.strip()


def build_search_queries(
    field: str,
    app_type: str,
    location: str = "",
    remote: bool = False,
) -> List[str]:
    """Build a broad set of search queries from structured user inputs.

    Generates queries across three angles:
      1. Direct company-site queries (field + hiring language)
      2. site: queries against job boards we now crawl for outbound links
      3. site: queries against ATS platforms (real company career pages)
    """
    field = field.strip()
    if not field:
        return []

    type_terms = APP_TYPE_TERMS.get(app_type, ["careers"])
    primary_term = type_terms[0]
    loc = location.strip()
    field_variants = _field_variants(field)

    queries: List[str] = []

    # 1. Direct company-site queries — one per field phrasing variant
    for fv in field_variants:
        base = f'{_phrase(fv)} {primary_term}'
        if loc:
            base += f" {loc}"
        if remote:
            base += " remote"
        queries.append(base)

    # Recruitment-focused variant (main field spelling only, avoid over-multiplying)
    queries.append(f'{_phrase(field)} {primary_term} contact email' + (f" {loc}" if loc else ""))

    # 2. Job-board-targeted queries — these boards are now crawled for
    # outbound company links rather than skipped, so hitting them directly
    # is a reliable way to surface real companies.
    for board in SITE_SEARCH_BOARDS:
        q = f'site:{board} {_phrase(field)}'
        if loc:
            q += f" {loc}"
        queries.append(q)

    # 3. ATS-platform queries — a hit is already a real company career page
    for ats in SITE_SEARCH_ATS:
        queries.append(f'site:{ats} {_phrase(field)}')

    # Startup / SME angle (good for internships)
    if app_type == "Internship":
        queries.append(f'{_phrase(field)} startup {primary_term}' + (f" {loc}" if loc else "") + (" remote" if remote else ""))

    # French queries when location suggests France
    if loc and any(w in loc.lower() for w in ["france", "paris", "lyon", "français", "fr "]):
        fr_term = "stage" if app_type == "Internship" else "recrutement"
        queries.append(f'{_phrase(field)} {fr_term} entreprise' + (f" {loc}" if loc else ""))
        queries.append(f'site:welcometothejungle.com {_phrase(field)} {fr_term}')

    # Deduplicate while preserving order
    seen: set = set()
    unique: List[str] = []
    for q in queries:
        q_norm = " ".join(q.split())
        if q_norm not in seen:
            seen.add(q_norm)
            unique.append(q_norm)
    return unique[:8]


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def get_country(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    for tld, country in TLD_COUNTRY_MAP.items():
        if netloc.endswith(tld):
            return country
    return "International"


def classify_email(local: str) -> str:
    lo = local.lower()
    if any(lo.startswith(p) for p in JOBS_PREFIXES):
        return "jobs"
    if any(lo.startswith(p) for p in CONTACT_PREFIXES):
        return "contact"
    if any(lo.startswith(p) for p in MEDIUM_PREFIXES):
        return "info"
    # Looks like firstname.lastname or firstname_lastname
    if re.match(r"^[a-z]{2,}[._\-][a-z]{2,}$", lo):
        return "person"
    return "generic"


def score_email(email: str, source_page: str = "", from_mailto: bool = False) -> int:
    """
    Score an email 0–100 based on usefulness for job applications.
    Higher = better.
    """
    if "@" not in email:
        return 0
    local, domain = email.lower().rsplit("@", 1)
    score = 40  # neutral baseline

    # Domain quality
    if domain in GENERIC_EMAIL_DOMAINS:
        score -= 35  # free email provider = not a company address
    elif domain in JUNK_EMAIL_DOMAINS:
        return 0
    else:
        score += 20  # proper company domain

    # Local-part quality
    if any(local.startswith(p) for p in JOBS_PREFIXES):
        score += 30   # direct recruitment target
    elif any(local.startswith(p) for p in CONTACT_PREFIXES):
        score += 20
    elif any(local.startswith(p) for p in MEDIUM_PREFIXES):
        score += 8
    elif any(local.startswith(p) for p in JUNK_EMAIL_PREFIXES):
        score -= 25
    elif re.match(r"^[a-z]{2,}[._\-][a-z]{2,}$", local):
        score += 12   # person name (firstname.lastname)

    # Source bonuses
    if from_mailto:
        score += 8    # intentionally published as clickable link
    if source_page and any(kw in source_page for kw in CONTACT_PAGE_KEYWORDS):
        score += 7

    return max(0, min(100, score))


# ─── COMPANY NAME EXTRACTION ──────────────────────────────────────────────────

def extract_company_name(soup: BeautifulSoup, url: str) -> str:
    domain_name = domain_to_company_name(url)
    candidates: List[str] = []

    # 1. OpenGraph site name (highest confidence)
    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        candidates.append(og["content"].strip())

    # 2. Schema.org Organisation types
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            if not script.string:
                continue
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") in (
                    "Organization", "LocalBusiness", "Corporation", "Company"
                ) and item.get("name"):
                    candidates.append(item["name"])
        except Exception:
            pass

    # 3. Clean page title (only if it looks like a real name)
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        if not is_bad_company_name(title):
            for sep in [" – ", " - ", " | ", " · ", " • ", ": ", " :: "]:
                parts = [p.strip() for p in title.split(sep) if p.strip()]
                if len(parts) > 1:
                    shortest = min(parts, key=len)
                    if not is_bad_company_name(shortest):
                        candidates.append(shortest)
                        break
            else:
                candidates.append(title)

    candidates.append(domain_name)

    for c in candidates:
        c = c.strip()
        if not is_bad_company_name(c):
            return c

    return domain_name


# ─── EMAIL EXTRACTION ─────────────────────────────────────────────────────────

def extract_emails_from_page(soup: BeautifulSoup, source_url: str = "") -> List[dict]:
    """
    Extract and score all emails found on a page.
    Returns list of dicts sorted by score (best first).
    """
    source_path = urlparse(source_url).path.lower() if source_url else ""
    raw: dict = {}  # email → {from_mailto, source_path}

    # 1. mailto links (most reliable – intentionally published)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?")[0].strip().lower()
            if email and "@" in email:
                raw[email] = {"from_mailto": True, "source_path": source_path}

    # 2. Regex in visible text
    text = soup.get_text(" ", strip=True)
    for e in re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,6}", text):
        e = e.lower().strip()
        if e not in raw:
            raw[e] = {"from_mailto": False, "source_path": source_path}

    # 3. Regex in raw HTML (catches JS-rendered / HTML-entity obfuscated emails)
    for e in re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,6}", str(soup)):
        e = e.lower().strip()
        if e not in raw:
            raw[e] = {"from_mailto": False, "source_path": source_path}

    results = []
    for e, meta in raw.items():
        if len(e) < 6 or len(e) > 80 or "@" not in e:
            continue
        local, domain = e.rsplit("@", 1)
        if not local or not domain:
            continue

        # Reject junk / image files / bad TLDs
        if any(d in domain for d in JUNK_EMAIL_DOMAINS):
            continue
        if any(e.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".js", ".css", ".webp")):
            continue
        if any(local.startswith(p) for p in JUNK_EMAIL_PREFIXES):
            continue
        tld = domain.rsplit(".", 1)[-1]
        if len(tld) < 2 or any(c.isdigit() for c in tld):
            continue

        s = score_email(e, source_page=meta["source_path"], from_mailto=meta["from_mailto"])
        results.append({
            "email":      e,
            "score":      s,
            "email_type": classify_email(local),
        })

    # Deduplicate, sort best-first
    seen: set = set()
    deduped: List[dict] = []
    for r in sorted(results, key=lambda x: -x["score"]):
        if r["email"] not in seen:
            seen.add(r["email"])
            deduped.append(r)

    return deduped


# ─── PAGE FETCHER ─────────────────────────────────────────────────────────────

def fetch_page(url: str, timeout: int = 12) -> Optional[BeautifulSoup]:
    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        if is_bot_challenge_page(soup):
            return None
        return soup
    except Exception:
        pass
    return None


# ─── CORE SCRAPER ─────────────────────────────────────────────────────────────

def scrape_company_url(url: str, debug_fn=None) -> List[dict]:
    """
    Scrape a single company URL.
    Tries main page first, then up to 4 contact/about/careers sub-pages.
    Returns up to 3 lead dicts (best-scored emails).
    """
    log = debug_fn or (lambda _: None)
    if is_blocked_domain(url):
        log(f"⏭️  Skipping blocked domain: {url}")
        return []

    soup = fetch_page(url)
    if soup is None:
        log(f"❌ Failed to load (or bot challenge): {url}")
        return []

    company_name = extract_company_name(soup, url)
    country      = get_country(url)
    email_data   = extract_emails_from_page(soup, url)

    # If main page has no emails, crawl contact/about/careers sub-pages
    if not email_data:
        log(f"  ↳ No emails on main page – checking sub-pages for {company_name}…")
        visited: set = {url}
        candidate_urls: List[str] = []

        # 1. Nav-link-derived candidates (original approach)
        for a in soup.find_all("a", href=True):
            href_raw = a["href"]
            href_lo  = href_raw.lower()
            text_lo  = a.get_text(" ", strip=True).lower()
            if not any(kw in href_lo or kw in text_lo for kw in CONTACT_PAGE_KEYWORDS):
                continue
            sub_url = urljoin(url, href_raw)
            if urlparse(sub_url).netloc != urlparse(url).netloc:
                continue
            candidate_urls.append(sub_url)

        # 2. Guessed common paths — catches sites whose nav is JS-rendered
        # and therefore invisible to a plain HTML parse (very common on
        # modern React/Next.js company sites).
        for path in ["/contact", "/contact-us", "/about", "/about-us",
                     "/careers", "/jobs", "/team", "/company"]:
            candidate_urls.append(urljoin(url, path))

        for sub_url in candidate_urls:
            if sub_url in visited:
                continue
            visited.add(sub_url)

            log(f"    ↳ Checking: {sub_url}")
            sub_soup = fetch_page(sub_url)
            if not sub_soup:
                continue
            found = extract_emails_from_page(sub_soup, sub_url)
            if found:
                email_data.extend(found)
                log(f"    ✅ {len(found)} email(s) on {sub_url}")
            if len(visited) >= 8:   # limit sub-page depth
                break

    if not email_data:
        log(f"  ⚠️ No useful emails for {company_name} ({url})")
        return []

    # Remove very-low quality emails (score < 10)
    email_data = [e for e in email_data if e["score"] >= 10]
    if not email_data:
        return []

    website = normalize_domain(url)
    leads = []
    for ed in email_data:
        if not email_matches_site(ed["email"], url):
            log(f"  ⏭️  Skipping {ed['email']} — domain doesn't match site ({website})")
            continue
        leads.append({
            "Select":     False,
            "Company":    company_name,
            "Website":    website,
            "Email":      ed["email"],
            "Email Type": ed["email_type"],
            "Score":      ed["score"],
            "Country":    country,
            "Status":     "Pending",
        })

    # Keep the top 3 emails per company (best diversity: jobs > contact > person)
    leads.sort(key=lambda x: -x["Score"])
    seen_types: set = set()
    top: List[dict] = []
    for lead in leads:
        if lead["Email Type"] not in seen_types or len(top) < 3:
            seen_types.add(lead["Email Type"])
            top.append(lead)
        if len(top) >= 3:
            break
    return top


def search_and_scrape(
    queries: List[str],
    max_results: int = 10,
    region: str = "wt-wt",
    debug_fn=None,
) -> List[dict]:
    """
    Full pipeline:
      1. Run structured search queries
      2. Filter aggregators / media / listicles
      3. Scrape company pages only
    """
    log = debug_fn or (lambda _: None)

    if not queries:
        log("⚠️ No search queries generated — fill in your field/role.")
        return []

    BACKENDS = ["duckduckgo", "google", "bing"]

    urls_seen: set = set()
    all_urls: List[str] = []

    for q in queries:
        for backend in BACKENDS:
            log(f'🔍 [{backend}] Searching: "{q}"')
            attempt_results = None
            for attempt in range(2):  # one retry on transient/rate-limit errors
                try:
                    attempt_results = list(DDGS().text(q, region=region, max_results=max_results, backend=backend))
                    break
                except Exception as exc:
                    if attempt == 0:
                        time.sleep(1.5)
                        continue
                    log(f"  ⚠️ {backend} error: {exc}")
            if attempt_results is None:
                time.sleep(0.4)
                continue
            added = 0
            for r in attempt_results:
                href = r.get("href", "")
                # Only hard-block here (social/aggregators) — job boards and
                # media pages are KEPT so we can crawl them for outbound links.
                if href and href not in urls_seen and not is_hard_blocked(href):
                    urls_seen.add(href)
                    all_urls.append(href)
                    added += 1
            log(f"  → {added} new URLs from {backend}")
            time.sleep(0.4)

    log(f"📋 {len(all_urls)} unique URLs to process")

    found_leads: List[dict] = []
    company_domains_seen: set = set()

    for url in all_urls:
        if is_hard_blocked(url):
            log(f"⏭️  Skipping blocked: {url}")
            continue

        parsed = urlparse(url)
        url_lower = url.lower()

        soup_main = fetch_page(url)
        if soup_main is None:
            log(f"❌ Unreachable or bot-protected: {url}")
            continue

        page_title = (soup_main.title.string or "").lower() if soup_main.title else ""
        is_extraction_source = is_link_extraction_source(url)
        is_listicle = is_extraction_source or any(kw in page_title for kw in LISTICLE_KEYWORDS)

        if is_listicle:
            kind = "Job board" if is_link_extraction_source(url) and not any(d in url_lower for d in MEDIA_DOMAINS) else "Media/listicle"
            log(f"📰 {kind} — extracting company links from: {url}")
            ext_links: set = set()
            for a in soup_main.find_all("a", href=True):
                href = a["href"]
                try:
                    h_netloc = urlparse(href).netloc
                    if (
                        href.startswith("http")
                        and h_netloc
                        and h_netloc != parsed.netloc
                        and not is_hard_blocked(href)
                        and not any(n in href.lower() for n in NOISE_LINK_DOMAINS)
                    ):
                        ext_links.add(href)
                except Exception:
                    pass
            # Job board listing pages tend to have many candidate links —
            # allow a bigger slice than a single blog/media article.
            slice_size = 20 if is_link_extraction_source(url) else 12
            target_urls: List[str] = sorted(ext_links, key=lambda x: len(urlparse(x).path))[:slice_size]
            log(f"  ➡️ Found {len(ext_links)} company links, visiting top {len(target_urls)}")
        else:
            target_urls = [url]

        for t_url in target_urls:
            if is_blocked_domain(t_url):
                continue
            t_netloc = normalize_domain(t_url)
            if t_netloc in company_domains_seen:
                continue

            log(f"🌐 Visiting: {t_url}")
            leads = scrape_company_url(t_url, debug_fn=log)
            if leads:
                company_domains_seen.add(t_netloc)
                found_leads.extend(leads)
                log(f"  🎉 {len(leads)} lead(s) from {t_netloc}")

    return found_leads


# ─── EMAIL SENDING ────────────────────────────────────────────────────────────

def get_email_content(company_name: str, country: str):  # -> Tuple[str, str]
    signature = f"\n{APPLICANT_NAME}\n{APPLICANT_PHONE}\n{APPLICANT_WEBSITE}"
    if APPLICANT_LINKEDIN:
        signature += f"\n{APPLICANT_LINKEDIN}"
    try:
        if country.lower() == "france":
            subject = EMAIL_SUBJECT_FR.replace("{company_name}", company_name)
            body    = EMAIL_BODY_FR.replace("{company_name}", company_name).replace("{signature}", signature)
        else:
            subject = EMAIL_SUBJECT_EN.replace("{company_name}", company_name)
            body    = EMAIL_BODY_EN.replace("{company_name}", company_name).replace("{signature}", signature)
    except Exception as e:
        subject = f"Application – {company_name}"
        body    = f"(Template error: {e})"
    return subject, body


def send_email(to_email: str, subject: str, body: str, attachment_path: Optional[str] = None) -> bool:
    msg = MIMEMultipart()
    msg["From"]    = MY_EMAIL
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if attachment_path and os.path.exists(attachment_path):
        try:
            with open(attachment_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            part["Content-Disposition"] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(part)
        except Exception as e:
            st.error(f"Attachment error: {e}")

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(MY_EMAIL, MY_APP_PASSWORD)
        server.sendmail(MY_EMAIL, to_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        st.error(f"Failed to send to {to_email}: {e}")
        return False


# ─── UI ───────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="AutoApply", page_icon="🚀", layout="wide")
st.title("🚀 AutoApply – Smart Lead Finder")
st.caption("Find real company emails, score them, and send personalised applications.")

# ══ 0. SCRAPING PANEL ════════════════════════════════════════════════════════

with st.expander("🔍 Auto-Scrape Leads from Web", expanded=True):
    st.info(
        "Fill in your **field** and **application type** — the bot builds targeted searches for you, "
        "including phrasing variants (e.g. *fullstack / full stack / full-stack*) and job-board searches "
        "(Indeed, Welcome to the Jungle, WeWorkRemotely, Greenhouse, Lever) whose listings it follows back "
        "to real company sites.  \n"
        "Scores **≥ 60** = high quality · **≥ 40** = worth reviewing · **< 40** = speculative  \n"
        "Broader field terms now run more searches, so a scrape can take a few minutes — that's expected."
    )

    row1_c1, row1_c2 = st.columns([2, 1])
    job_field = row1_c1.text_input(
        "Your field / role",
        "Data Science",
        placeholder="e.g. Data Science, FinTech, Machine Learning, Software Engineering",
        help="What you do or want to do — used to find relevant companies",
    )
    app_type = row1_c2.selectbox(
        "Application type",
        list(APP_TYPE_TERMS.keys()),
        help="Internship, full-time, or spontaneous application",
    )

    row2_c1, row2_c2, row2_c3 = st.columns([2, 2, 1])
    location = row2_c1.text_input(
        "Location (optional)",
        "",
        placeholder="e.g. Paris, France, London, Berlin",
        help="City or country — leave empty for worldwide",
    )
    remote = row2_c2.checkbox(
        "Include remote / worldwide companies",
        value=True,
        help="Adds 'remote' to searches for distributed-friendly companies",
    )
    max_results = row2_c3.number_input("Max URLs", min_value=5, max_value=40, value=10, step=5)

    debug_mode = st.checkbox("Show debug logs")

    preview_queries = build_search_queries(job_field, app_type, location, remote)
    if preview_queries:
        st.caption("**Searches that will run:**")
        for pq in preview_queries:
            st.code(pq, language=None)

    start_btn = st.button("🚀 Start Scraping", type="primary", disabled=not job_field.strip())

if start_btn:
    log_lines = []  # type: List[str]

    status_box = st.status("Initialising…", expanded=True)
    log_box    = st.empty()

    def ui_log(msg: str) -> None:
        log_lines.append(msg)
        if debug_mode:
            log_box.text("\n".join(log_lines[-40:]))

    region = "fr-fr" if location and any(
        w in location.lower() for w in ["paris", "france", "lyon", "marseille", "français", "fr "]
    ) else "wt-wt"
    queries = build_search_queries(job_field, app_type, location, remote)

    with status_box as s:
        s.write(f"🔍 **Field:** {job_field} · **Type:** {app_type} · **Region:** {region}")
        found_leads = search_and_scrape(queries, max_results=max_results, region=region, debug_fn=ui_log)
        s.update(label=f"✅ Scraping done – {len(found_leads)} lead(s) found", state="complete", expanded=False)

    if found_leads:
        new_df = pd.DataFrame(found_leads, columns=COLUMNS)
        new_df = new_df.drop_duplicates(subset=["Email"])
        st.session_state.leads = (
            pd.concat([st.session_state.leads, new_df], ignore_index=True)
            .drop_duplicates(subset=["Email"])
            .reset_index(drop=True)
        )
        save_leads()
        st.success(f"Added **{len(found_leads)}** new lead(s). Scroll down to review them.")
    else:
        st.warning(
            "No emails found. Try a different field, add a location, or increase Max URLs. "
            "News sites and startup directories are automatically excluded."
        )

# ══ 1. ADD LEAD MANUALLY ═════════════════════════════════════════════════════

with st.expander("➕ Add Lead Manually"):
    mc1, mc2, mc3, mc4 = st.columns([2, 2, 2, 1])
    new_company = mc1.text_input("Company Name")
    new_email   = mc2.text_input("Email Address")
    new_website = mc3.text_input("Website (optional)")
    new_country = mc4.selectbox("Country", ["International", "France", "Germany", "United Kingdom", "Other"])
    if st.button("Add Lead"):
        if new_email and "@" in new_email:
            local = new_email.split("@")[0].lower()
            new_row = {
                "Select":     False,
                "Company":    new_company or new_email.split("@")[1].split(".")[0].title(),
                "Website":    new_website or new_email.split("@")[1],
                "Email":      new_email.lower().strip(),
                "Email Type": classify_email(local),
                "Score":      score_email(new_email.lower().strip()),
                "Country":    new_country,
                "Status":     "Pending",
            }
            st.session_state.leads = pd.concat(
                [st.session_state.leads, pd.DataFrame([new_row])], ignore_index=True
            )
            save_leads()
            st.success(f"Lead added! (Score: {new_row['Score']})")
        else:
            st.error("Please enter a valid email address.")

# ══ 2. MANAGE LEADS ══════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("📋 Manage Leads")

total  = len(st.session_state.leads)
pending_df = st.session_state.leads[st.session_state.leads["Status"] == "Pending"] if total else pd.DataFrame()
high_q = len(pending_df[pending_df["Score"].fillna(0).astype(float) >= 60]) if len(pending_df) else 0

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total Leads", total)
m2.metric("Pending", len(pending_df))
m3.metric("High Quality (≥60)", high_q)
sent_count = len(st.session_state.leads[st.session_state.leads["Status"].str.startswith("Sent", na=False)]) if total else 0
m4.metric("Sent", sent_count)

# Action buttons row
ba1, ba2, ba3, ba4, ba5 = st.columns([2, 2, 2, 2, 2])

if ba1.button("✅ Select High-Score (≥ 60)"):
    mask = (st.session_state.leads["Score"].fillna(0).astype(float) >= 60) & \
           (st.session_state.leads["Status"] == "Pending")
    st.session_state.leads.loc[mask, "Select"] = True
    save_leads()
    st.rerun()

if ba2.button("☑️ Select All Pending"):
    st.session_state.leads.loc[st.session_state.leads["Status"] == "Pending", "Select"] = True
    save_leads()
    st.rerun()

if ba3.button("🧹 Clean Duplicates"):
    before = len(st.session_state.leads)
    st.session_state.leads = (
        st.session_state.leads
        .sort_values("Score", ascending=False)
        .drop_duplicates(subset=["Email"])
        .reset_index(drop=True)
    )
    save_leads()
    st.success(f"Removed {before - len(st.session_state.leads)} duplicate(s).")
    st.rerun()

if ba4.button("🔧 Fix & Clean Bad Leads"):
    before = len(st.session_state.leads)
    keep_rows = []
    fixed = 0
    for _, row in st.session_state.leads.iterrows():
        email = str(row.get("Email", ""))
        website = str(row.get("Website", ""))
        email_dom = normalize_domain(email.rsplit("@", 1)[1]) if "@" in email else ""
        if is_blocked_domain(website) or is_blocked_domain(email_dom):
            continue
        name = str(row.get("Company", ""))
        if is_bad_company_name(name) and website:
            row = row.copy()
            row["Company"] = domain_to_company_name(website)
            fixed += 1
        keep_rows.append(row)
    st.session_state.leads = pd.DataFrame(keep_rows, columns=COLUMNS).reset_index(drop=True)
    removed = before - len(st.session_state.leads)
    save_leads()
    st.success(f"Removed {removed} bad/aggregator lead(s), fixed {fixed} company name(s).")
    st.rerun()

if ba5.button("🗑️ Clear All Leads"):
    st.session_state.leads = pd.DataFrame(columns=COLUMNS)
    save_leads()
    st.rerun()

# Score filter
score_filter = 0
if total > 0:
    score_filter = st.slider(
        "Filter by minimum Score",
        min_value=0, max_value=100, value=0, step=5,
        help="Hide leads below this score to focus on quality contacts",
    )
    display_df = st.session_state.leads[
        st.session_state.leads["Score"].fillna(0).astype(float) >= score_filter
    ].reset_index(drop=True)
else:
    display_df = st.session_state.leads

st.info("📝 You can edit Company and Email directly in the table. Check a row to select it for sending.")

edited_df = st.data_editor(
    display_df,
    column_config={
        "Select": st.column_config.CheckboxColumn("Select", help="Select to send email", default=False),
        "Score": st.column_config.ProgressColumn(
            "Score",
            help="Email quality score (0–100). Higher = more likely to be a real contact.",
            min_value=0,
            max_value=100,
            format="%d",
        ),
        "Email Type": st.column_config.SelectboxColumn(
            "Email Type",
            options=["jobs", "contact", "info", "person", "generic"],
            help="jobs = recruitment address, contact = general contact, person = named person",
        ),
        "Status": st.column_config.TextColumn("Status", disabled=True),
        "Website": st.column_config.TextColumn("Website", disabled=True),
    },
    disabled=["Status", "Website", "Email Type", "Score"],
    hide_index=True,
    width="stretch",
    key="leads_editor",
)

# Sync edits back (only for the visible/filtered subset)
if not edited_df.equals(display_df):
    if score_filter > 0:
        # Merge edited rows back into full dataset
        mask = st.session_state.leads["Score"].fillna(0).astype(float) >= score_filter
        st.session_state.leads.loc[mask] = edited_df.values
    else:
        st.session_state.leads = edited_df
    save_leads()
    st.rerun()

# Export button
if total > 0:
    csv_bytes = st.session_state.leads.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Export leads to CSV",
        data=csv_bytes,
        file_name=f"leads_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

# ══ 3. EMAIL OPERATIONS ══════════════════════════════════════════════════════

st.markdown("---")
st.subheader("📧 Email Operations")

# Count selected rows (across ALL leads, not just filtered)
selected_rows = st.session_state.leads[st.session_state.leads["Select"] == True]
selected_count = len(selected_rows)

cfg_col1, cfg_col2 = st.columns(2)
delay_sec = cfg_col1.slider("Delay between emails (seconds)", 1, 10, 2)
dry_run   = cfg_col2.checkbox("Dry run (preview only – don't actually send)", value=True)

if MY_EMAIL and MY_APP_PASSWORD:
    send_btn_label = f"📤 Send to {selected_count} selected" if not dry_run else f"👁️ Preview {selected_count} selected"
    if st.button(send_btn_label, type="primary", disabled=selected_count == 0):
        progress = st.progress(0)
        results  = []
        for i, (idx, row) in enumerate(selected_rows.iterrows()):
            subj, body = get_email_content(str(row["Company"]), str(row["Country"]))
            attach     = CV_FILE_FR if str(row.get("Country", "")) == "France" else CV_FILE_EN

            if dry_run:
                with st.expander(f"📬 Preview: {row['Email']}"):
                    st.text(f"To: {row['Email']}\nSubject: {subj}\n\n{body}")
                results.append((idx, True))
            else:
                ok = send_email(row["Email"], subj, body, attachment_path=attach)
                if ok:
                    st.session_state.leads.at[idx, "Status"]  = f"Sent {datetime.now().strftime('%Y-%m-%d')}"
                    st.session_state.leads.at[idx, "Select"]  = False
                    st.toast(f"✅ Sent to {row['Company']}")
                else:
                    st.toast(f"❌ Failed: {row['Company']}")
                results.append((idx, ok))
                time.sleep(delay_sec)

            progress.progress((i + 1) / selected_count)

        if not dry_run:
            save_leads()
            st.success(f"Batch done – {sum(1 for _, ok in results if ok)} sent.")
            st.rerun()
else:
    st.warning("⚠️ Email sending not configured. Add `MY_EMAIL` and `MY_APP_PASSWORD` to your `.env` file.")

# ══ 4. TEMPLATE PREVIEW (read-only) ═══════════════════════════════════════════

st.markdown("---")
with st.expander("✉️ Email Template Preview (read-only)", expanded=False):
    st.markdown(
        "This shows **exactly what will be sent** when you email a lead — pulled from your `.env` file.  \n"
        "It is **not editable here** on purpose: templates are personal config, not app data.  \n"
        "To change the wording, edit `EMAIL_SUBJECT_EN/FR` and `EMAIL_BODY_EN/FR` in `.env`, then refresh this page."
    )
    prev_c1, prev_c2 = st.columns(2)
    lang = prev_c1.radio("Language", ["International (English)", "France (French)"], horizontal=True)
    sample_company = prev_c2.text_input("Preview company name", "Acme Corp")
    country_preview = "France" if "France" in lang else "International"
    p_subj, p_body = get_email_content(sample_company, country_preview)
    st.text_input("Subject", p_subj, disabled=True)
    st.text_area("Body", p_body, height=260, disabled=True)