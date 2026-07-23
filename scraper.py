"""
AutoApply — web scraping, domain classification, and email scoring.
Headless-safe: no Streamlit imports.
"""

import json
import re
import time
from typing import Callable, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS

# ─── DOMAIN / KEYWORD LISTS ───────────────────────────────────────────────────

SOCIAL_DOMAINS = {
    "linkedin.com", "youtube.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "tiktok.com", "pinterest.com", "reddit.com",
    "quora.com", "snapchat.com",
}

JOB_BOARD_DOMAINS = {
    "indeed.com", "glassdoor.com", "welcometothejungle.com",
    "monster.com", "ziprecruiter.com", "simplyhired.com", "careerbuilder.com",
    "jooble.org", "talent.com", "remote.co", "weworkremotely.com",
    "themuse.com", "jobteaser.com", "hellowork.com", "apec.fr",
    "wttj.co", "remoteok.com", "jobicy.com", "workingnomads.com",
    "otta.com", "himalayas.app",
}

ATS_DOMAINS = {
    "greenhouse.io", "lever.co", "workable.com", "breezy.hr",
    "smartrecruiters.com", "ashbyhq.com", "bamboohr.com",
    "teamtailor.com", "recruitee.com", "personio.com", "jobvite.com",
    "myworkdayjobs.com",
}

SKIP_DOMAINS = SOCIAL_DOMAINS

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

APP_TYPE_TERMS = {
    "Internship": ["internship", "intern", "stage", "alternance"],
    "Full-time job": ["hiring", "careers", "jobs", "recrutement", "emploi"],
    "Spontaneous application": ["careers", "contact", "join us", "recrutement"],
}

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

SITE_SEARCH_BOARDS = ["indeed.com", "welcometothejungle.com", "weworkremotely.com"]
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

# UI column names used by the Streamlit dashboard
LEAD_COLUMNS = ["Select", "Company", "Website", "Email", "Email Type", "Score", "Country", "Status"]


def normalize_domain(url_or_domain: str) -> str:
    """Return bare domain without a leading 'www.'."""
    if "://" in url_or_domain:
        url_or_domain = urlparse(url_or_domain).netloc
    domain = url_or_domain.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def is_hard_blocked(url_or_domain: str) -> bool:
    """Never worth visiting at all — social media & pure startup directories."""
    domain = normalize_domain(url_or_domain)
    blocked = SOCIAL_DOMAINS | AGGREGATOR_DOMAINS
    return any(d in domain for d in blocked)


def is_link_extraction_source(url_or_domain: str) -> bool:
    """Job boards & media pages: crawl for outbound company links."""
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

    def root(d: str) -> str:
        parts = d.split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else d

    return root(email_domain) == root(site_domain)


def domain_to_company_name(domain: str) -> str:
    """Turn 'data-recrutement.fr' → 'Data Recrutement'."""
    root = normalize_domain(domain).split(".")[0]
    return re.sub(r"[-_]", " ", root).title()


def _field_variants(field: str) -> List[str]:
    field = field.strip()
    variants = [field]
    key = field.lower()
    for alt in FIELD_SYNONYMS.get(key, []):
        if alt.lower() not in (v.lower() for v in variants):
            variants.append(alt)
    return variants


def _phrase(term: str) -> str:
    return f'"{term}"' if " " in term.strip() else term.strip()


def build_search_queries(
    field: str,
    app_type: str,
    location: str = "",
    remote: bool = False,
) -> List[str]:
    """Build a broad set of search queries from structured user inputs."""
    field = field.strip()
    if not field:
        return []

    type_terms = APP_TYPE_TERMS.get(app_type, ["careers"])
    primary_term = type_terms[0]
    loc = location.strip()
    field_variants = _field_variants(field)

    queries: List[str] = []

    for fv in field_variants:
        base = f"{_phrase(fv)} {primary_term}"
        if loc:
            base += f" {loc}"
        if remote:
            base += " remote"
        queries.append(base)

    queries.append(f"{_phrase(field)} {primary_term} contact email" + (f" {loc}" if loc else ""))

    for board in SITE_SEARCH_BOARDS:
        q = f"site:{board} {_phrase(field)}"
        if loc:
            q += f" {loc}"
        queries.append(q)

    for ats in SITE_SEARCH_ATS:
        queries.append(f"site:{ats} {_phrase(field)}")

    if app_type == "Internship":
        queries.append(
            f"{_phrase(field)} startup {primary_term}"
            + (f" {loc}" if loc else "")
            + (" remote" if remote else "")
        )

    if loc and any(w in loc.lower() for w in ["france", "paris", "lyon", "français", "fr "]):
        fr_term = "stage" if app_type == "Internship" else "recrutement"
        queries.append(f"{_phrase(field)} {fr_term} entreprise" + (f" {loc}" if loc else ""))
        queries.append(f"site:welcometothejungle.com {_phrase(field)} {fr_term}")

    seen: set = set()
    unique: List[str] = []
    for q in queries:
        q_norm = " ".join(q.split())
        if q_norm not in seen:
            seen.add(q_norm)
            unique.append(q_norm)
    return unique[:8]


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
    if re.match(r"^[a-z]{2,}[._\-][a-z]{2,}$", lo):
        return "person"
    return "generic"


def score_email(email: str, source_page: str = "", from_mailto: bool = False) -> int:
    """Score an email 0–100 based on usefulness for job applications."""
    if "@" not in email:
        return 0
    local, domain = email.lower().rsplit("@", 1)
    score = 40

    if domain in GENERIC_EMAIL_DOMAINS:
        score -= 35
    elif domain in JUNK_EMAIL_DOMAINS:
        return 0
    else:
        score += 20

    if any(local.startswith(p) for p in JOBS_PREFIXES):
        score += 30
    elif any(local.startswith(p) for p in CONTACT_PREFIXES):
        score += 20
    elif any(local.startswith(p) for p in MEDIUM_PREFIXES):
        score += 8
    elif any(local.startswith(p) for p in JUNK_EMAIL_PREFIXES):
        score -= 25
    elif re.match(r"^[a-z]{2,}[._\-][a-z]{2,}$", local):
        score += 12

    if from_mailto:
        score += 8
    if source_page and any(kw in source_page for kw in CONTACT_PAGE_KEYWORDS):
        score += 7

    return max(0, min(100, score))


def extract_page_context(soup: BeautifulSoup) -> str:
    """Title + short text snippet from a scraped page, used for LLM drafting."""
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    snippet = soup.get_text(" ", strip=True)[:500]
    if title:
        return f"{title}\n{snippet}"
    return snippet


def extract_company_name(soup: BeautifulSoup, url: str) -> str:
    domain_name = domain_to_company_name(url)
    candidates: List[str] = []

    og = soup.find("meta", property="og:site_name")
    if og and og.get("content"):
        candidates.append(og["content"].strip())

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


def extract_emails_from_page(soup: BeautifulSoup, source_url: str = "") -> List[dict]:
    """Extract and score all emails found on a page."""
    source_path = urlparse(source_url).path.lower() if source_url else ""
    raw: dict = {}

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            email = href[7:].split("?")[0].strip().lower()
            if email and "@" in email:
                raw[email] = {"from_mailto": True, "source_path": source_path}

    text = soup.get_text(" ", strip=True)
    for e in re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,6}", text):
        e = e.lower().strip()
        if e not in raw:
            raw[e] = {"from_mailto": False, "source_path": source_path}

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
            "email": e,
            "score": s,
            "email_type": classify_email(local),
        })

    seen: set = set()
    deduped: List[dict] = []
    for r in sorted(results, key=lambda x: -x["score"]):
        if r["email"] not in seen:
            seen.add(r["email"])
            deduped.append(r)

    return deduped


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


def scrape_company_url(url: str, debug_fn: Optional[Callable[[str], None]] = None) -> List[dict]:
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
    country = get_country(url)
    page_context = extract_page_context(soup)
    email_data = extract_emails_from_page(soup, url)

    if not email_data:
        log(f"  ↳ No emails on main page – checking sub-pages for {company_name}…")
        visited: set = {url}
        candidate_urls: List[str] = []

        for a in soup.find_all("a", href=True):
            href_raw = a["href"]
            href_lo = href_raw.lower()
            text_lo = a.get_text(" ", strip=True).lower()
            if not any(kw in href_lo or kw in text_lo for kw in CONTACT_PAGE_KEYWORDS):
                continue
            sub_url = urljoin(url, href_raw)
            if urlparse(sub_url).netloc != urlparse(url).netloc:
                continue
            candidate_urls.append(sub_url)

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
                sub_context = extract_page_context(sub_soup)
                if len(sub_context) > len(page_context):
                    page_context = sub_context
                log(f"    ✅ {len(found)} email(s) on {sub_url}")
            if len(visited) >= 8:
                break

    if not email_data:
        log(f"  ⚠️ No useful emails for {company_name} ({url})")
        return []

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
            "Select": False,
            "Company": company_name,
            "Website": website,
            "Email": ed["email"],
            "Email Type": ed["email_type"],
            "Score": ed["score"],
            "Country": country,
            "Status": "Pending",
            "page_context": page_context,
        })

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
    debug_fn: Optional[Callable[[str], None]] = None,
) -> List[dict]:
    """Run structured search queries, filter noise, scrape company pages."""
    log = debug_fn or (lambda _: None)

    if not queries:
        log("⚠️ No search queries generated — fill in your field/role.")
        return []

    backends = ["duckduckgo", "google", "bing"]

    urls_seen: set = set()
    all_urls: List[str] = []

    for q in queries:
        for backend in backends:
            log(f'🔍 [{backend}] Searching: "{q}"')
            attempt_results = None
            for attempt in range(2):
                try:
                    attempt_results = list(
                        DDGS().text(q, region=region, max_results=max_results, backend=backend)
                    )
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
            kind = (
                "Job board"
                if is_link_extraction_source(url) and not any(d in url_lower for d in MEDIA_DOMAINS)
                else "Media/listicle"
            )
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
