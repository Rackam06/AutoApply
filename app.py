"""
AutoApply — Smart Lead Finder & Email Bot
Scrape company emails, score them, draft with local LLM, and send applications.
"""

import os
import random
import smtplib
import time
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional, Tuple

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import db
from cv_utils import (
    build_cv_text_for_llm,
    cv_file_status,
    cv_for_country,
    resolve_cv_paths,
    save_cv_upload,
)
from scraper import (
    APP_TYPE_TERMS,
    build_search_queries,
    classify_email,
    domain_to_company_name,
    is_bad_company_name,
    is_blocked_domain,
    normalize_domain,
    score_email,
    search_and_scrape,
)

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
load_dotenv()

MY_EMAIL = os.getenv("MY_EMAIL")
MY_APP_PASSWORD = os.getenv("MY_APP_PASSWORD")
APPLICANT_NAME = os.getenv("APPLICANT_NAME", "Jane Doe")
APPLICANT_PHONE = os.getenv("APPLICANT_PHONE", "+1 234 567 890")
APPLICANT_WEBSITE = os.getenv("APPLICANT_WEBSITE", "www.janedoe.com")
APPLICANT_LINKEDIN = os.getenv("APPLICANT_LINKEDIN", "")

CV_FILE_FR = os.getenv("CV_FILE_FR", "docs/CV_French.pdf")
CV_FILE_EN = os.getenv("CV_FILE_EN", "docs/CV_English.pdf")

EMAIL_SUBJECT_FR = os.getenv("EMAIL_SUBJECT_FR", "Candidature Spontanée – {company_name}")
EMAIL_BODY_FR = os.getenv(
    "EMAIL_BODY_FR",
    "Bonjour,\n\nJe vous contacte afin de soumettre ma candidature spontanée chez {company_name}.\n\n"
    "Cordialement,\n{signature}",
).replace("\\n", "\n")
EMAIL_SUBJECT_EN = os.getenv("EMAIL_SUBJECT_EN", "Internship Application – {company_name}")
EMAIL_BODY_EN = os.getenv(
    "EMAIL_BODY_EN",
    "Dear Hiring Manager,\n\nI would like to apply for an internship at {company_name}.\n\n"
    "Best regards,\n{signature}",
).replace("\\n", "\n")

DAILY_EMAIL_CAP = int(os.getenv("DAILY_EMAIL_CAP", "50"))

READY_COLUMNS = [
    "Select", "Company", "Website", "Email", "Email Type", "Score", "Country",
    "Draft Subject", "Draft Body", "Status",
]

db.migrate_csv_if_needed()


def _today_sent_count() -> int:
    conn = db.get_connection()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE status = 'sent' AND sent_at LIKE ?",
            (f"{today}%",),
        ).fetchone()
        return int(row["n"]) if row else 0
    finally:
        conn.close()


def get_selection_state() -> dict:
    if "selections" not in st.session_state:
        st.session_state.selections = {}
    return st.session_state.selections


def load_ready_df() -> pd.DataFrame:
    leads = db.get_leads(draft_status="ready_to_send", status="pending")
    rows = db.leads_to_dataframe_rows(leads)
    selections = get_selection_state()
    for row in rows:
        lid = row["id"]
        row["Select"] = selections.get(lid, False)
    if not rows:
        return pd.DataFrame(columns=READY_COLUMNS)
    df = pd.DataFrame(rows)
    for col in READY_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col != "Select" else False
    return df[READY_COLUMNS + (["id"] if "id" in df.columns else [])]


def sync_ready_edits(edited: pd.DataFrame, original: pd.DataFrame) -> None:
    """Persist table edits back to SQLite."""
    if edited.equals(original):
        return
    selections = get_selection_state()
    for i, row in edited.iterrows():
        orig = original.iloc[i]
        lead_id = int(orig.get("id") or row.get("id") or 0)
        if not lead_id:
            continue
        selections[lead_id] = bool(row.get("Select", False))
        db.update_lead(
            lead_id,
            company=str(row.get("Company", "")),
            email=str(row.get("Email", "")).lower().strip(),
            draft_subject=str(row.get("Draft Subject", "")),
            draft_body=str(row.get("Draft Body", "")),
        )
    st.session_state.selections = selections


def get_email_content(company_name: str, country: str) -> Tuple[str, str]:
    """Fallback template when no draft exists (legacy preview)."""
    profile = db.get_profile()
    signature = db.profile_signature_block(profile)
    if not signature:
        signature = f"{APPLICANT_NAME}\n{APPLICANT_PHONE}\n{APPLICANT_WEBSITE}"
        if APPLICANT_LINKEDIN:
            signature += f"\n{APPLICANT_LINKEDIN}"
    signature = f"\n{signature}"
    try:
        if country.lower() == "france":
            subject = EMAIL_SUBJECT_FR.replace("{company_name}", company_name)
            body = EMAIL_BODY_FR.replace("{company_name}", company_name).replace("{signature}", signature)
        else:
            subject = EMAIL_SUBJECT_EN.replace("{company_name}", company_name)
            body = EMAIL_BODY_EN.replace("{company_name}", company_name).replace("{signature}", signature)
    except Exception as e:
        subject = f"Application – {company_name}"
        body = f"(Template error: {e})"
    return subject, body


def send_email(
    to_email: str,
    subject: str,
    body: str,
    attachment_path: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    msg = MIMEMultipart()
    msg["From"] = MY_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if attachment_path and os.path.exists(attachment_path):
        try:
            with open(attachment_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            part["Content-Disposition"] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(part)
        except Exception as e:
            return False, f"Attachment error: {e}"

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(MY_EMAIL, MY_APP_PASSWORD)
        server.sendmail(MY_EMAIL, to_email, msg.as_string())
        server.quit()
        return True, None
    except Exception as e:
        return False, str(e)


def send_delay_with_jitter(base_delay: float) -> None:
    jitter = random.uniform(0, base_delay * 0.5)
    time.sleep(base_delay + jitter)


# ─── UI ───────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="AutoApply", page_icon="🚀", layout="wide")
st.title("🚀 AutoApply – Smart Lead Finder")
st.caption("Find real company emails, draft with local LLM, and send personalised applications.")

# ══ 0. PROFILE ═══════════════════════════════════════════════════════════════

st.subheader("👤 Your Profile")
st.caption(
    "Name, phone, and bio are used by the outreach worker for drafting "
    "(exact name spelling matters; phone in the signature raises reply rates). "
    "CVs are PDF attachments — upload here or place them in `docs/`. "
    "Save before starting the worker."
)

profile = db.get_profile()
en_path, fr_path = resolve_cv_paths(profile)

# Prefill from .env when profile fields are still empty
_env_name = (APPLICANT_NAME or "").strip().split(None, 1)
_default_first = profile.get("first_name") or (_env_name[0] if _env_name else "")
_default_last = profile.get("last_name") or (_env_name[1] if len(_env_name) > 1 else "")
_default_phone = profile.get("phone") or (APPLICANT_PHONE or "")
_default_website = profile.get("website") or (APPLICANT_WEBSITE or "")
_default_linkedin = profile.get("linkedin") or (APPLICANT_LINKEDIN or "")

name_c1, name_c2 = st.columns(2)
with name_c1:
    first_name_input = st.text_input("First name", value=_default_first, placeholder="Wail")
with name_c2:
    last_name_input = st.text_input("Last name", value=_default_last, placeholder="Ameur")

contact_c1, contact_c2, contact_c3 = st.columns(3)
with contact_c1:
    phone_input = st.text_input("Phone", value=_default_phone, placeholder="+33 6 12 34 56 78")
with contact_c2:
    website_input = st.text_input("Website", value=_default_website, placeholder="www.example.com")
with contact_c3:
    linkedin_input = st.text_input("LinkedIn", value=_default_linkedin, placeholder="https://linkedin.com/in/…")

bio_input = st.text_area(
    "Bio",
    value=profile.get("bio", ""),
    height=120,
    placeholder="Short professional bio — background, skills, what you're looking for…",
)

cv_c1, cv_c2 = st.columns(2)
with cv_c1:
    st.markdown("**English CV** (attached for non-French leads)")
    st.caption(cv_file_status(en_path))
    cv_en_upload = st.file_uploader(
        "Upload English CV (PDF)",
        type=["pdf"],
        key="cv_en_upload",
        label_visibility="collapsed",
    )
with cv_c2:
    st.markdown("**French CV** (attached for France leads)")
    st.caption(cv_file_status(fr_path))
    cv_fr_upload = st.file_uploader(
        "Upload French CV (PDF)",
        type=["pdf"],
        key="cv_fr_upload",
        label_visibility="collapsed",
    )

if st.button("💾 Save Profile", type="primary"):
    saved_en = en_path
    saved_fr = fr_path
    if cv_en_upload is not None:
        saved_en = save_cv_upload(cv_en_upload.getvalue(), CV_FILE_EN)
    if cv_fr_upload is not None:
        saved_fr = save_cv_upload(cv_fr_upload.getvalue(), CV_FILE_FR)

    updated_profile = {
        **profile,
        "first_name": first_name_input.strip(),
        "last_name": last_name_input.strip(),
        "phone": phone_input.strip(),
        "website": website_input.strip(),
        "linkedin": linkedin_input.strip(),
        "bio": bio_input.strip(),
        "cv_file_en": saved_en,
        "cv_file_fr": saved_fr,
    }
    cv_text = build_cv_text_for_llm(updated_profile)
    db.save_profile(
        bio_input.strip(),
        first_name=first_name_input.strip(),
        last_name=last_name_input.strip(),
        phone=phone_input.strip(),
        website=website_input.strip(),
        linkedin=linkedin_input.strip(),
        cv_file_en=saved_en,
        cv_file_fr=saved_fr,
        cv_text=cv_text,
    )
    st.success("Profile saved.")
    st.rerun()

if profile.get("updated_at"):
    st.caption(f"Last saved: {profile['updated_at']}")
    preview_sig = db.profile_signature_block({
        **profile,
        "first_name": first_name_input.strip() or profile.get("first_name", ""),
        "last_name": last_name_input.strip() or profile.get("last_name", ""),
        "phone": phone_input.strip() or profile.get("phone", ""),
        "website": website_input.strip() or profile.get("website", ""),
        "linkedin": linkedin_input.strip() or profile.get("linkedin", ""),
    })
    if preview_sig:
        st.code(preview_sig, language=None)

# ══ 1. SCRAPING PANEL ════════════════════════════════════════════════════════

with st.expander("🔍 Auto-Scrape Leads from Web", expanded=False):
    st.info(
        "Fill in your **field** and **application type** — the bot builds targeted searches for you. "
        "Scores **≥ 60** = high quality · **≥ 40** = worth reviewing · **< 40** = speculative. "
        "For unattended drafting, use `python outreach_worker.py` instead."
    )

    row1_c1, row1_c2 = st.columns([2, 1])
    job_field = row1_c1.text_input(
        "Your field / role",
        "Data Science",
        placeholder="e.g. Data Science, FinTech, Machine Learning",
    )
    app_type = row1_c2.selectbox("Application type", list(APP_TYPE_TERMS.keys()))

    row2_c1, row2_c2, row2_c3 = st.columns([2, 2, 1])
    location = row2_c1.text_input("Location (optional)", "", placeholder="e.g. Paris, France")
    remote = row2_c2.checkbox("Include remote / worldwide companies", value=True)
    max_results = row2_c3.number_input("Max URLs", min_value=5, max_value=40, value=10, step=5)

    debug_mode = st.checkbox("Show debug logs")

    preview_queries = build_search_queries(job_field, app_type, location, remote)
    if preview_queries:
        st.caption("**Searches that will run:**")
        for pq in preview_queries:
            st.code(pq, language=None)

    start_btn = st.button("🚀 Start Scraping", type="primary", disabled=not job_field.strip())

if start_btn:
    log_lines: List[str] = []
    status_box = st.status("Initialising…", expanded=True)
    log_box = st.empty()

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

    added = 0
    for raw in found_leads:
        lead_id = db.insert_lead({
            "company": raw.get("Company", ""),
            "website": normalize_domain(raw.get("Website") or ""),
            "email": raw.get("Email", "").lower().strip(),
            "email_type": raw.get("Email Type", ""),
            "score": int(raw.get("Score") or 0),
            "country": raw.get("Country", ""),
            "page_context": raw.get("page_context", ""),
            "draft_status": "none",
            "status": "pending",
        })
        if lead_id:
            added += 1

    if added:
        st.success(f"Added **{added}** new lead(s) to the database.")
        st.rerun()
    elif found_leads:
        st.info("All scraped leads were already in the database (duplicates skipped).")
    else:
        st.warning("No emails found. Try a different field, add a location, or increase Max URLs.")

# ══ 2. ADD LEAD MANUALLY ═════════════════════════════════════════════════════

with st.expander("➕ Add Lead Manually"):
    mc1, mc2, mc3, mc4 = st.columns([2, 2, 2, 1])
    new_company = mc1.text_input("Company Name")
    new_email = mc2.text_input("Email Address")
    new_website = mc3.text_input("Website (optional)")
    new_country = mc4.selectbox("Country", ["International", "France", "Germany", "United Kingdom", "Other"])
    if st.button("Add Lead"):
        if new_email and "@" in new_email:
            local = new_email.split("@")[0].lower()
            email = new_email.lower().strip()
            website = new_website or email.split("@")[1]
            lead_id = db.insert_lead({
                "company": new_company or domain_to_company_name(website),
                "website": normalize_domain(website),
                "email": email,
                "email_type": classify_email(local),
                "score": score_email(email),
                "country": new_country,
                "draft_status": "none",
                "status": "pending",
            })
            if lead_id:
                st.success(f"Lead added! (Score: {score_email(email)})")
                st.rerun()
            else:
                st.warning("Lead already exists (duplicate email).")
        else:
            st.error("Please enter a valid email address.")

# ══ 3. METRICS ═══════════════════════════════════════════════════════════════

st.markdown("---")
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Ready to Send", db.count_leads(draft_status="ready_to_send"))
m2.metric("Flagged", db.count_leads(draft_status="flagged"))
m3.metric("Pending Send", db.count_leads(draft_status="ready_to_send", status="pending"))
m4.metric("Sent Today", _today_sent_count())
m5.metric("Total in DB", db.count_leads())

# ══ 4. READY TO SEND ═════════════════════════════════════════════════════════

st.subheader("📋 Ready to Send")
st.info(
    "Drafts queued by the outreach worker (or added manually). "
    "Edit subject/body if you want — no mandatory review step. Check rows to send."
)

ready_df = load_ready_df()
if len(ready_df) == 0:
    st.caption("No drafts ready yet. Start the worker or scrape leads manually.")
else:
    display_ready = ready_df.drop(columns=["id"], errors="ignore")
    edited_ready = st.data_editor(
        display_ready,
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", help="Select to send email"),
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "Status": st.column_config.TextColumn("Status", disabled=True),
            "Website": st.column_config.TextColumn("Website", disabled=True),
            "Email Type": st.column_config.TextColumn("Email Type", disabled=True),
            "Draft Subject": st.column_config.TextColumn("Draft Subject"),
            "Draft Body": st.column_config.TextColumn("Draft Body", width="large"),
        },
        disabled=["Status", "Website", "Email Type", "Score", "Country"],
        hide_index=True,
        width="stretch",
        key="ready_editor",
    )

    if not edited_ready.equals(display_ready):
        sync_ready_edits(
            pd.concat([edited_ready, ready_df[["id"]]], axis=1),
            ready_df,
        )
        st.rerun()

# ══ 5. FLAGGED ════════════════════════════════════════════════════════════════

flagged_leads = db.get_leads(draft_status="flagged")
with st.expander(f"🚩 Flagged ({len(flagged_leads)})", expanded=len(flagged_leads) > 0 and len(ready_df) == 0):
    if not flagged_leads:
        st.caption("No flagged drafts — the worker only flags leads it couldn't self-review cleanly.")
    else:
        flagged_rows = []
        for lead in flagged_leads:
            flagged_rows.append({
                "Company": lead.get("company", ""),
                "Email": lead.get("email", ""),
                "Score": lead.get("score", 0),
                "Review Notes": lead.get("self_review_notes") or "",
                "Draft Subject": lead.get("draft_subject") or "",
                "Draft Body": (lead.get("draft_body") or "")[:200] + "…"
                if len(lead.get("draft_body") or "") > 200
                else (lead.get("draft_body") or ""),
            })
        st.dataframe(pd.DataFrame(flagged_rows), hide_index=True, width="stretch")

# Export
all_leads = db.get_leads()
if all_leads:
    export_rows = []
    for lead in all_leads:
        export_rows.append({
            "Company": lead.get("company"),
            "Website": lead.get("website"),
            "Email": lead.get("email"),
            "Email Type": lead.get("email_type"),
            "Score": lead.get("score"),
            "Country": lead.get("country"),
            "Draft Status": lead.get("draft_status"),
            "Draft Subject": lead.get("draft_subject"),
            "Draft Body": lead.get("draft_body"),
            "Send Status": lead.get("status"),
            "Review Notes": lead.get("self_review_notes"),
        })
    csv_bytes = pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Export all leads to CSV",
        data=csv_bytes,
        file_name=f"leads_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

# Maintenance buttons
ba1, ba2, ba3 = st.columns(3)
if ba1.button("✅ Select All Ready"):
    ready_leads = db.get_leads(draft_status="ready_to_send", status="pending")
    selections = get_selection_state()
    for lead in ready_leads:
        selections[lead["id"]] = True
    st.session_state.selections = selections
    st.rerun()

if ba2.button("🔧 Fix Bad Company Names"):
    fixed = 0
    for lead in db.get_leads():
        name = str(lead.get("company") or "")
        website = str(lead.get("website") or "")
        if is_bad_company_name(name) and website:
            db.update_lead(lead["id"], company=domain_to_company_name(website))
            fixed += 1
    st.success(f"Fixed {fixed} company name(s).")
    st.rerun()

if ba3.button("🧹 Remove Blocked Domains"):
    removed = 0
    for lead in db.get_leads():
        website = str(lead.get("website") or "")
        email = str(lead.get("email") or "")
        email_dom = normalize_domain(email.rsplit("@", 1)[1]) if "@" in email else ""
        if is_blocked_domain(website) or is_blocked_domain(email_dom):
            db.delete_lead(lead["id"])
            removed += 1
    st.success(f"Removed {removed} blocked lead(s).")
    st.rerun()

# ══ 6. EMAIL OPERATIONS ══════════════════════════════════════════════════════

st.markdown("---")
st.subheader("📧 Email Operations")

ready_df = load_ready_df()
selected_rows = ready_df[ready_df["Select"] == True] if len(ready_df) else pd.DataFrame()
selected_count = len(selected_rows)

cfg_col1, cfg_col2, cfg_col3 = st.columns(3)
delay_sec = cfg_col1.slider("Delay between emails (seconds)", 1, 10, 2)
dry_run = cfg_col2.checkbox("Dry run (preview only – don't actually send)", value=True)
st.caption(f"Daily cap: {DAILY_EMAIL_CAP} · Sent today: {_today_sent_count()}")

if MY_EMAIL and MY_APP_PASSWORD:
    send_btn_label = (
        f"📤 Send to {selected_count} selected"
        if not dry_run
        else f"👁️ Preview {selected_count} selected"
    )
    if st.button(send_btn_label, type="primary", disabled=selected_count == 0):
        sent_today = _today_sent_count()
        progress = st.progress(0)
        success_count = 0

        for i, (_, row) in enumerate(selected_rows.iterrows()):
            if not dry_run and sent_today + success_count >= DAILY_EMAIL_CAP:
                st.warning(f"Daily cap of {DAILY_EMAIL_CAP} reached — stopping batch.")
                break

            subj = str(row.get("Draft Subject") or "")
            body = str(row.get("Draft Body") or "")
            if not subj or not body:
                subj, body = get_email_content(str(row["Company"]), str(row["Country"]))

            attach = cv_for_country(profile, str(row.get("Country", "")))
            lead_id = int(row.get("id") or 0)

            if dry_run:
                with st.expander(f"📬 Preview: {row['Email']}"):
                    st.text(f"To: {row['Email']}\nSubject: {subj}\n\n{body}")
                success_count += 1
            else:
                ok, err = send_email(row["Email"], subj, body, attachment_path=attach)
                if ok:
                    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                    db.update_lead(
                        lead_id,
                        status="sent",
                        sent_at=now,
                    )
                    selections = get_selection_state()
                    selections[lead_id] = False
                    st.session_state.selections = selections
                    st.toast(f"✅ Sent to {row['Company']}")
                    success_count += 1
                else:
                    db.update_lead(lead_id, status="failed")
                    st.toast(f"❌ Failed: {row['Company']} — {err}")

                if i < selected_count - 1:
                    send_delay_with_jitter(delay_sec)

            progress.progress((i + 1) / selected_count)

        if not dry_run:
            st.success(f"Batch done – {success_count} sent.")
            st.rerun()
else:
    st.warning("⚠️ Email sending not configured. Add `MY_EMAIL` and `MY_APP_PASSWORD` to your `.env` file.")

# ══ 7. TEMPLATE PREVIEW (read-only) ═══════════════════════════════════════════

st.markdown("---")
with st.expander("✉️ Legacy Template Preview (read-only)", expanded=False):
    st.markdown(
        "Fallback templates from `.env` — used only when a lead has no draft. "
        "The worker generates per-lead drafts instead."
    )
    prev_c1, prev_c2 = st.columns(2)
    lang = prev_c1.radio("Language", ["International (English)", "France (French)"], horizontal=True)
    sample_company = prev_c2.text_input("Preview company name", "Acme Corp")
    country_preview = "France" if "France" in lang else "International"
    p_subj, p_body = get_email_content(sample_company, country_preview)
    st.text_input("Subject", p_subj, disabled=True)
    st.text_area("Body", p_body, height=260, disabled=True)
