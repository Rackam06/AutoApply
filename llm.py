"""
Ollama-backed LLM helpers for contact classification, drafting, and self-review.
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

from cv_utils import build_cv_text_for_llm
from db import profile_full_name, profile_signature_block

load_dotenv()

log = logging.getLogger("llm")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b-it-qat")
# Read timeout — CPU gemma3:4b drafts often need 2–4 min (~3 tok/s).
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))

# Prompt / generation caps — oversized CV context is what made drafts hang.
BIO_CHARS = 800
CV_CHARS = 900
PAGE_CHARS = 600
CLASSIFY_NUM_PREDICT = 96
DRAFT_NUM_PREDICT = 350
REVIEW_NUM_PREDICT = 400
PLAN_NUM_PREDICT = 500
PLAN_CV_CHARS = 1600

VALID_APP_TYPES = ("Internship", "Full-time job", "Spontaneous application")


def _timeout() -> Tuple[float, float]:
    """(connect, read) — fail fast if server is down, allow long generation."""
    return (10, OLLAMA_TIMEOUT)


def _extract_json(content: str) -> Dict[str, Any]:
    """Parse JSON from model output; tolerate markdown fences if present."""
    text = (content or "").strip()
    if not text:
        raise ValueError("Empty response from Ollama")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise
        return json.loads(match.group(0))


def _chat_json(
    system: str,
    user: str,
    *,
    num_predict: int,
    temperature: float = 0.2,
    label: str = "chat",
) -> Dict[str, Any]:
    """
    Call Ollama /api/chat with JSON output.

    Retries once on connection / parse errors only — NOT on read timeout.
    Timing out and immediately retrying stacks a second job behind the still-
    running first one on CPU, which is what made the worker look "stuck".

    think=false is required for thinking models (e.g. gemma4:e4b): otherwise
    num_predict is spent on internal reasoning and content comes back empty.
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "think": False,
        "keep_alive": "10m",
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            # Smaller context = faster prompt eval on CPU
            "num_ctx": 2048,
        },
    }
    last_err: Optional[Exception] = None
    for attempt in range(2):
        t0 = time.time()
        try:
            resp = requests.post(
                f"{OLLAMA_HOST}/api/chat",
                json=payload,
                timeout=_timeout(),
            )
            resp.raise_for_status()
            data = resp.json()
            message = data.get("message") or {}
            content = message.get("content") or ""
            # Thinking models may still leak reasoning into a side field
            if not content and message.get("thinking"):
                raise ValueError(
                    "Ollama returned empty content (only thinking tokens). "
                    "Ensure think=false is supported, or raise num_predict."
                )
            wall = time.time() - t0
            log.info(
                "%s ok in %.1fs (prompt=%d chars, eval=%s, attempt=%d)",
                label,
                wall,
                len(system) + len(user),
                data.get("eval_count"),
                attempt + 1,
            )
            return _extract_json(content)
        except requests.Timeout as exc:
            wall = time.time() - t0
            log.warning(
                "%s timed out after %.1fs — not retrying (Ollama may still be "
                "generating; raise OLLAMA_TIMEOUT or use a faster model). %s",
                label,
                wall,
                exc,
            )
            raise RuntimeError(
                f"Ollama timed out after {wall:.0f}s (OLLAMA_TIMEOUT={OLLAMA_TIMEOUT}). "
                f"On CPU, gemma3:4b drafts often need 200–300s. Try raising "
                f"OLLAMA_TIMEOUT or use a smaller/faster model."
            ) from exc
        except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
            last_err = exc
            wall = time.time() - t0
            log.warning("%s failed in %.1fs (attempt %d): %s", label, wall, attempt + 1, exc)
            if attempt == 0:
                time.sleep(1.0)
                continue
            raise RuntimeError(f"Ollama request failed after retry: {last_err}") from last_err
    raise RuntimeError("Ollama request failed")


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def pre_filter_draft(
    subject: str,
    body: str,
    company: str,
    full_name: str = "",
    phone: str = "",
) -> Optional[str]:
    """
    Cheap deterministic pre-filter before self_review.
    Returns rejection reason string, or None if draft passes.
    """
    combined = f"{subject}\n{body}"
    if "[" in combined or "{{" in combined:
        return "Unresolved placeholders detected"
    wc = word_count(body)
    if wc < 40:
        return f"Body too short ({wc} words, need 40+)"
    if wc > 250:
        return f"Body too long ({wc} words, max 250)"
    if not company or not company.strip():
        return "Missing company name for validation"
    company_lower = company.strip().lower()
    body_lower = (body or "").lower()
    if company_lower not in body_lower:
        tokens = [t for t in re.split(r"[\s\-_]+", company_lower) if len(t) > 2]
        if not any(t in body_lower for t in tokens):
            return f"Company name '{company}' not mentioned in body"
    # Require exact last-name spelling when provided (blocks Amour vs Ameur)
    name = (full_name or "").strip()
    if name:
        parts = name.split()
        last = parts[-1] if len(parts) > 1 else ""
        if last and last.lower() not in body_lower:
            return f"Applicant last name '{last}' missing or misspelled in body"
    phone_clean = (phone or "").strip()
    if phone_clean:
        # Match on digit sequence so "+33 6 95 02 72 99" ≈ "33695027299"
        phone_digits = re.sub(r"\D", "", phone_clean)
        body_digits = re.sub(r"\D", "", body or "")
        if phone_digits and phone_digits not in body_digits:
            return f"Phone number '{phone_clean}' missing from signature"
    return None


def ensure_signature(body: str, profile: Dict[str, str]) -> str:
    """
    Append a contact signature (name + phone [+ website/linkedin]) when missing.
    Deterministic — do not rely on the model remembering the phone number.
    """
    text = (body or "").rstrip()
    sig = profile_signature_block(profile)
    if not sig:
        return text

    phone = (profile.get("phone") or "").strip()
    phone_digits = re.sub(r"\D", "", phone)
    body_digits = re.sub(r"\D", "", text)
    has_phone = bool(phone_digits) and phone_digits in body_digits

    full_name = profile_full_name(profile)
    has_name = bool(full_name) and full_name.lower() in text.lower()

    if has_phone and has_name:
        return text

    # If model already signed with the name but forgot the phone, append remaining lines
    if has_name and phone and not has_phone:
        extra = []
        if phone_digits not in body_digits:
            extra.append(phone)
        for line in ((profile.get("website") or "").strip(), (profile.get("linkedin") or "").strip()):
            if line and line.lower() not in text.lower():
                extra.append(line)
        if extra:
            return text + "\n" + "\n".join(extra)
        return text

    return text + "\n\n" + sig


def classify_contact(company: str, email: str, page_context: str) -> Dict[str, str]:
    """Classify contact quality: personal | generic | invalid."""
    system = (
        "You classify job-application email contacts. "
        "Respond with JSON only: {\"quality\": \"personal\"|\"generic\"|\"invalid\", \"reason\": \"...\"}. "
        "personal = named person or direct hiring contact; "
        "generic = info@, contact@, jobs@; "
        "invalid = noreply, bounce, unrelated domain, or clearly wrong address."
    )
    user = (
        f"Company: {company}\n"
        f"Email: {email}\n"
        f"Page context:\n{(page_context or '')[:PAGE_CHARS]}"
    )
    result = _chat_json(system, user, num_predict=CLASSIFY_NUM_PREDICT, label="classify")
    quality = str(result.get("quality", "generic")).lower()
    if quality not in ("personal", "generic", "invalid"):
        quality = "generic"
    return {"quality": quality, "reason": str(result.get("reason", ""))}


def draft_email(
    profile: Dict[str, str],
    lead: Dict[str, Any],
    field: str,
    app_type: str,
) -> Dict[str, str]:
    """Draft a tailored outreach email subject and body."""
    bio = (profile.get("bio") or "")[:BIO_CHARS]
    cv = build_cv_text_for_llm(profile)[:CV_CHARS]
    company = lead.get("company", "")
    page_context = (lead.get("page_context") or "")[:PAGE_CHARS]
    full_name = profile_full_name(profile)
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    phone = (profile.get("phone") or "").strip()
    website = (profile.get("website") or "").strip()
    linkedin = (profile.get("linkedin") or "").strip()
    signature = profile_signature_block(profile)

    system = (
        "You write concise, professional job-application emails. "
        "Respond with JSON only: {\"subject\": \"...\", \"body\": \"...\"}. "
        "Rules: 80-180 words in body; one clear ask matching the application type; "
        "mention the company by its real name; use only facts from page_context and profile; "
        "no placeholders like [Company]; professional but human tone; plain text, no markdown. "
        "End with a signature block using the EXACT name and phone provided "
        "(phone on the line after the name). Never invent or approximate the name spelling."
    )
    name_block = (
        f"Applicant name (spell EXACTLY like this everywhere, including signature): "
        f"{full_name or '(not set)'}\n"
        f"First name: {first or '(not set)'}\n"
        f"Last name: {last or '(not set)'}\n"
        f"Phone (MUST appear in the signature, exact digits): {phone or '(not set)'}\n"
        f"Website: {website or '(optional)'}\n"
        f"LinkedIn: {linkedin or '(optional)'}\n"
        f"Required signature block to end the email with:\n{signature or '(name + phone)'}\n\n"
    )
    user = (
        f"{name_block}"
        f"Applicant bio:\n{bio}\n\n"
        f"CV highlights:\n{cv}\n\n"
        f"Target field: {field}\n"
        f"Application type: {app_type}\n"
        f"Company: {company}\n"
        f"Contact email: {lead.get('email', '')}\n"
        f"Country: {lead.get('country', '')}\n"
        f"Page context (only source for company-specific claims):\n{page_context}"
    )
    result = _chat_json(
        system, user, num_predict=DRAFT_NUM_PREDICT, temperature=0.3, label="draft"
    )
    body = ensure_signature(str(result.get("body", "")).strip(), profile)
    return {
        "subject": str(result.get("subject", "")).strip(),
        "body": body,
    }


def self_review(
    profile: Dict[str, str],
    lead: Dict[str, Any],
    draft: Dict[str, str],
    field: str,
    app_type: str,
) -> Dict[str, Any]:
    """
    Judge AND fix a draft in one call.
    Returns pass, issues, and always-populated revised_subject/revised_body.
    """
    bio = (profile.get("bio") or "")[:BIO_CHARS]
    company = lead.get("company", "")
    page_context = (lead.get("page_context") or "")[:PAGE_CHARS]
    full_name = profile_full_name(profile)
    phone = (profile.get("phone") or "").strip()
    signature = profile_signature_block(profile)

    system = (
        "You are a strict editor for job-application emails. "
        "Respond with JSON only:\n"
        "{\n"
        '  "pass": true|false,\n'
        '  "issues": ["..."],\n'
        '  "revised_subject": "...",\n'
        '  "revised_body": "..."\n'
        "}\n\n"
        "Rubric — the email must:\n"
        "1. Name the actual company correctly; no [Company] or template placeholders.\n"
        "2. Not invent specifics about the company beyond page_context.\n"
        "3. Be 80-180 words with one clear ask matching field/app_type.\n"
        "4. Sound professional but human, not templated.\n"
        "5. Ground claims in the applicant profile — relevant background only.\n"
        "6. Sign / refer to the applicant with the EXACT full name provided "
        "(letter-for-letter). Fix any misspelling.\n"
        "7. Signature must include the phone number on the line after the name "
        "when a phone is provided — this is required for callback responses.\n\n"
        "Always provide revised_subject and revised_body (your fixed version), even if pass=false."
    )
    user = (
        f"Applicant full name (must appear spelled exactly): {full_name or '(not set)'}\n"
        f"First name: {(profile.get('first_name') or '').strip() or '(not set)'}\n"
        f"Last name: {(profile.get('last_name') or '').strip() or '(not set)'}\n"
        f"Phone (must appear in signature): {phone or '(not set)'}\n"
        f"Required signature block:\n{signature or '(name + phone)'}\n\n"
        f"Applicant bio:\n{bio}\n\n"
        f"Field: {field}\nApplication type: {app_type}\n"
        f"Company: {company}\n"
        f"Page context:\n{page_context}\n\n"
        f"Draft subject: {draft.get('subject', '')}\n"
        f"Draft body:\n{draft.get('body', '')}"
    )
    result = _chat_json(
        system, user, num_predict=REVIEW_NUM_PREDICT, temperature=0.2, label="self_review"
    )
    issues = result.get("issues") or []
    if isinstance(issues, str):
        issues = [issues]
    revised_body = ensure_signature(
        str(result.get("revised_body", draft.get("body", ""))).strip(),
        profile,
    )
    return {
        "pass": bool(result.get("pass")),
        "issues": [str(i) for i in issues],
        "revised_subject": str(result.get("revised_subject", draft.get("subject", ""))).strip(),
        "revised_body": revised_body,
    }


def suggest_search_plan(profile: Dict[str, str]) -> Dict[str, Any]:
    """
    Derive a diversified job-search plan from bio + CV.

    Returns:
      {
        "summary": str,
        "searches": [
          {"field": str, "app_type": str, "location": str, "remote": bool, "why": str},
          ...
        ]
      }
    """
    bio = (profile.get("bio") or "")[:BIO_CHARS]
    cv = build_cv_text_for_llm(profile)[:PLAN_CV_CHARS]
    full_name = profile_full_name(profile)

    if not bio.strip() and not cv.strip():
        raise ValueError(
            "Profile bio/CV empty — fill Your Profile in the dashboard before auto-planning."
        )

    system = (
        "You are a career search strategist. Given an applicant bio and CV highlights, "
        "propose a diversified set of job SEARCH ANGLES — not just the obvious title. "
        "People who only search 'Data Science' compete with everyone; niche or adjacent "
        "titles that match real projects often convert better.\n\n"
        "Respond with JSON only:\n"
        "{\n"
        '  "summary": "1-2 sentence overview of this candidates positioning",\n'
        '  "searches": [\n'
        "    {\n"
        '      "field": "short search phrase used in job boards (2-5 words)",\n'
        '      "app_type": "Internship" | "Full-time job" | "Spontaneous application",\n'
        '      "location": "" or city/country if strongly implied,\n'
        '      "remote": true|false,\n'
        '      "why": "one sentence tying this angle to their experience"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Return 4 to 6 searches, ranked best-first.\n"
        "- Diversify: mix core title, adjacent titles, domain angles (e.g. FinTech), "
        "and skill-based angles (e.g. Python developer, LLM engineer).\n"
        "- field must be a concrete searchable phrase employers actually post "
        "(e.g. 'Machine Learning Engineer', not 'someone who likes AI').\n"
        "- Prefer Full-time job unless bio clearly seeks internship/stage.\n"
        "- Set remote=true when they want remote or are location-flexible.\n"
        "- Do not invent employers or degrees not in the profile."
    )
    user = (
        f"Applicant: {full_name or '(name not set)'}\n\n"
        f"Bio:\n{bio or '(empty)'}\n\n"
        f"CV highlights:\n{cv or '(empty)'}"
    )
    result = _chat_json(
        system, user, num_predict=PLAN_NUM_PREDICT, temperature=0.4, label="search_plan"
    )

    raw_searches = result.get("searches") or []
    if isinstance(raw_searches, dict):
        raw_searches = list(raw_searches.values())

    searches: list[Dict[str, Any]] = []
    seen_fields: set[str] = set()
    for item in raw_searches:
        if not isinstance(item, dict):
            continue
        field = " ".join(str(item.get("field") or "").split()).strip()
        if not field:
            continue
        key = field.lower()
        if key in seen_fields:
            continue
        seen_fields.add(key)

        app_type = str(item.get("app_type") or "Full-time job").strip()
        if app_type not in VALID_APP_TYPES:
            # Fuzzy map common variants
            lo = app_type.lower()
            if "intern" in lo or "stage" in lo:
                app_type = "Internship"
            elif "spontan" in lo:
                app_type = "Spontaneous application"
            else:
                app_type = "Full-time job"

        remote = item.get("remote")
        if isinstance(remote, str):
            remote = remote.strip().lower() in ("1", "true", "yes", "y")
        else:
            remote = bool(remote) if remote is not None else True

        searches.append({
            "field": field,
            "app_type": app_type,
            "location": str(item.get("location") or "").strip(),
            "remote": remote,
            "why": str(item.get("why") or "").strip(),
        })
        if len(searches) >= 6:
            break

    if not searches:
        raise ValueError("LLM returned no usable search angles — retry or set --field manually.")

    return {
        "summary": str(result.get("summary") or "").strip(),
        "searches": searches,
    }
