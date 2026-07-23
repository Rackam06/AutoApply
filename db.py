"""
SQLite persistence for AutoApply leads and profile.
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_FILE = os.getenv("LEADS_DB", "leads.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    company TEXT,
    website TEXT,
    email TEXT UNIQUE,
    email_type TEXT,
    score INTEGER,
    country TEXT,
    page_context TEXT,
    contact_quality TEXT,
    draft_subject TEXT,
    draft_body TEXT,
    draft_status TEXT DEFAULT 'none',
    self_review_notes TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    sent_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_website_email
    ON leads(website, email);

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    website TEXT DEFAULT '',
    linkedin TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    cv_file_en TEXT DEFAULT '',
    cv_file_fr TEXT DEFAULT '',
    cv_text TEXT DEFAULT '',
    updated_at TEXT
);
"""


def _migrate_profile_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(profile)").fetchall()}
    for col in (
        "cv_file_en",
        "cv_file_fr",
        "first_name",
        "last_name",
        "phone",
        "website",
        "linkedin",
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE profile ADD COLUMN {col} TEXT DEFAULT ''")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DB_FILE
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _migrate_profile_schema(conn)
    conn.commit()
    return conn


def lead_exists(domain: str, email: Optional[str] = None, conn: Optional[sqlite3.Connection] = None) -> bool:
    """Check if a lead already exists by website domain or email."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        domain = domain.lower().strip()
        if email:
            row = conn.execute(
                "SELECT 1 FROM leads WHERE lower(website) = ? OR lower(email) = ? LIMIT 1",
                (domain, email.lower().strip()),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM leads WHERE lower(website) = ? LIMIT 1",
                (domain,),
            ).fetchone()
        return row is not None
    finally:
        if own_conn:
            conn.close()


def insert_lead(lead: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> Optional[int]:
    """Insert a lead. Returns new row id, or None if duplicate."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        created_at = lead.get("created_at") or _now_iso()
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO leads (
                company, website, email, email_type, score, country,
                page_context, contact_quality, draft_subject, draft_body,
                draft_status, self_review_notes, status, created_at, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lead.get("company", ""),
                lead.get("website", ""),
                lead.get("email", "").lower().strip(),
                lead.get("email_type", ""),
                int(lead.get("score") or 0),
                lead.get("country", ""),
                lead.get("page_context", ""),
                lead.get("contact_quality"),
                lead.get("draft_subject"),
                lead.get("draft_body"),
                lead.get("draft_status", "none"),
                lead.get("self_review_notes"),
                lead.get("status", "pending"),
                created_at,
                lead.get("sent_at"),
            ),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        return cur.lastrowid
    finally:
        if own_conn:
            conn.close()


def get_leads(
    draft_status: Optional[str] = None,
    status: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Fetch leads, optionally filtered by draft_status and/or send status."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        clauses: List[str] = []
        params: List[Any] = []
        if draft_status is not None:
            clauses.append("draft_status = ?")
            params.append(draft_status)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM leads {where} ORDER BY score DESC, created_at DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own_conn:
            conn.close()


def get_lead(lead_id: int, conn: Optional[sqlite3.Connection] = None) -> Optional[Dict[str, Any]]:
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if own_conn:
            conn.close()


def update_lead(lead_id: int, conn: Optional[sqlite3.Connection] = None, **fields: Any) -> None:
    """Update arbitrary lead columns by keyword."""
    if not fields:
        return
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        allowed = {
            "company", "website", "email", "email_type", "score", "country",
            "page_context", "contact_quality", "draft_subject", "draft_body",
            "draft_status", "self_review_notes", "status", "sent_at",
        }
        sets = []
        values = []
        for key, val in fields.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            values.append(val)
        if not sets:
            return
        values.append(lead_id)
        conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def delete_lead(lead_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def count_leads(
    draft_status: Optional[str] = None,
    status: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        clauses: List[str] = []
        params: List[Any] = []
        if draft_status is not None:
            clauses.append("draft_status = ?")
            params.append(draft_status)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = conn.execute(f"SELECT COUNT(*) AS n FROM leads {where}", params).fetchone()
        return int(row["n"]) if row else 0
    finally:
        if own_conn:
            conn.close()


def get_profile(conn: Optional[sqlite3.Connection] = None) -> Dict[str, str]:
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT first_name, last_name, phone, website, linkedin, bio, "
            "cv_file_en, cv_file_fr, cv_text, updated_at "
            "FROM profile WHERE id = 1"
        ).fetchone()
        empty = {
            "first_name": "",
            "last_name": "",
            "phone": "",
            "website": "",
            "linkedin": "",
            "bio": "",
            "cv_file_en": "",
            "cv_file_fr": "",
            "cv_text": "",
            "updated_at": "",
        }
        if not row:
            return empty
        return {
            "first_name": row["first_name"] or "",
            "last_name": row["last_name"] or "",
            "phone": row["phone"] or "",
            "website": row["website"] or "",
            "linkedin": row["linkedin"] or "",
            "bio": row["bio"] or "",
            "cv_file_en": row["cv_file_en"] or "",
            "cv_file_fr": row["cv_file_fr"] or "",
            "cv_text": row["cv_text"] or "",
            "updated_at": row["updated_at"] or "",
        }
    finally:
        if own_conn:
            conn.close()


def save_profile(
    bio: str,
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    website: str = "",
    linkedin: str = "",
    cv_file_en: str = "",
    cv_file_fr: str = "",
    cv_text: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO profile (
                id, first_name, last_name, phone, website, linkedin,
                bio, cv_file_en, cv_file_fr, cv_text, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                phone = excluded.phone,
                website = excluded.website,
                linkedin = excluded.linkedin,
                bio = excluded.bio,
                cv_file_en = excluded.cv_file_en,
                cv_file_fr = excluded.cv_file_fr,
                cv_text = excluded.cv_text,
                updated_at = excluded.updated_at
            """,
            (
                first_name.strip(),
                last_name.strip(),
                phone.strip(),
                website.strip(),
                linkedin.strip(),
                bio,
                cv_file_en,
                cv_file_fr,
                cv_text,
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def profile_full_name(profile: Dict[str, str]) -> str:
    return " ".join(
        p for p in [(profile.get("first_name") or "").strip(), (profile.get("last_name") or "").strip()] if p
    )


def profile_signature_block(profile: Dict[str, str]) -> str:
    """Plain-text signature: name, then phone, then optional website/linkedin."""
    lines = []
    name = profile_full_name(profile)
    if name:
        lines.append(name)
    phone = (profile.get("phone") or "").strip()
    if phone:
        lines.append(phone)
    website = (profile.get("website") or "").strip()
    if website:
        lines.append(website)
    linkedin = (profile.get("linkedin") or "").strip()
    if linkedin:
        lines.append(linkedin)
    return "\n".join(lines)


def leads_to_dataframe_rows(leads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert DB rows to dashboard-friendly dicts."""
    rows = []
    for lead in leads:
        send_status = lead.get("status") or "pending"
        display_status = "Pending"
        if send_status.startswith("sent"):
            display_status = f"Sent {lead.get('sent_at', '')[:10]}" if lead.get("sent_at") else "Sent"
        elif send_status == "failed":
            display_status = "Failed"
        rows.append({
            "id": lead["id"],
            "Select": False,
            "Company": lead.get("company") or "",
            "Website": lead.get("website") or "",
            "Email": lead.get("email") or "",
            "Email Type": lead.get("email_type") or "",
            "Score": int(lead.get("score") or 0),
            "Country": lead.get("country") or "",
            "Status": display_status,
            "Draft Subject": lead.get("draft_subject") or "",
            "Draft Body": lead.get("draft_body") or "",
            "self_review_notes": lead.get("self_review_notes") or "",
            "draft_status": lead.get("draft_status") or "none",
        })
    return rows


def migrate_csv_if_needed(csv_path: str = "leads.csv", conn: Optional[sqlite3.Connection] = None) -> int:
    """One-time import from legacy leads.csv if DB is empty."""
    if not os.path.exists(csv_path):
        return 0
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    try:
        existing = conn.execute("SELECT COUNT(*) AS n FROM leads").fetchone()
        if existing and existing["n"] > 0:
            return 0
        import pandas as pd

        df = pd.read_csv(csv_path)
        imported = 0
        for _, row in df.iterrows():
            email = str(row.get("Email", "")).strip().lower()
            if not email or "@" not in email:
                continue
            status_raw = str(row.get("Status", "Pending")).lower()
            status = "pending"
            sent_at = None
            if status_raw.startswith("sent"):
                status = "sent"
                sent_at = _now_iso()
            lead_id = insert_lead({
                "company": str(row.get("Company", "")),
                "website": str(row.get("Website", "")),
                "email": email,
                "email_type": str(row.get("Email Type", "")),
                "score": int(row.get("Score") or 0),
                "country": str(row.get("Country", "")),
                "status": status,
                "sent_at": sent_at,
            }, conn=conn)
            if lead_id:
                imported += 1
        return imported
    finally:
        if own_conn:
            conn.close()
