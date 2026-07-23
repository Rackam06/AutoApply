#!/usr/bin/env python3
"""
AutoApply outreach worker — continuously finds leads, drafts emails via local LLM,
self-reviews drafts, and queues ready ones in leads.db.

Usage:
    python outreach_worker.py --field "Data Science" --type "Full-time job" --remote
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

from dotenv import load_dotenv

import db
import llm
from cv_utils import build_cv_text_for_llm, cv_file_status, resolve_cv_paths
from scraper import build_search_queries, normalize_domain, search_and_scrape

load_dotenv()

LOG_FILE = "worker.log"


def setup_logging() -> logging.Logger:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
        fh.setFormatter(fmt)
        root.addHandler(fh)
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    # Surface llm timing lines in the same log
    logging.getLogger("llm").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger("outreach_worker")


def region_for_location(location: str) -> str:
    if location and any(
        w in location.lower() for w in ["paris", "france", "lyon", "marseille", "français", "fr "]
    ):
        return "fr-fr"
    return "wt-wt"


def process_lead_draft(
    profile: Dict[str, str],
    lead_row: Dict[str, Any],
    field: str,
    app_type: str,
    logger: logging.Logger,
) -> str:
    """
    Run classify → draft → pre-filter → self_review pipeline.
    Returns final draft_status: ready_to_send | flagged | rejected.
    """
    lead_id = lead_row["id"]
    company = lead_row.get("company") or ""
    email = lead_row.get("email") or ""

    try:
        classification = llm.classify_contact(company, email, lead_row.get("page_context") or "")
        contact_quality = classification["quality"]
        db.update_lead(lead_id, contact_quality=contact_quality)

        if contact_quality == "invalid":
            db.update_lead(
                lead_id,
                draft_status="rejected",
                self_review_notes=classification.get("reason") or "Invalid contact",
            )
            return "rejected"
    except Exception as exc:
        logger.warning("classify_contact failed for %s: %s", company, exc)
        contact_quality = "generic"
        db.update_lead(lead_id, contact_quality=contact_quality)

    notes: list[str] = []
    final_status = "flagged"

    for attempt in range(2):
        try:
            draft = llm.draft_email(profile, lead_row, field, app_type)
        except Exception as exc:
            notes.append(f"draft attempt {attempt + 1} error: {exc}")
            continue

        reject_reason = llm.pre_filter_draft(
            draft["subject"],
            draft["body"],
            company,
            full_name=db.profile_full_name(profile),
            phone=profile.get("phone") or "",
        )
        if reject_reason:
            notes.append(f"pre-filter attempt {attempt + 1}: {reject_reason}")
            continue

        try:
            review = llm.self_review(profile, lead_row, draft, field, app_type)
        except Exception as exc:
            notes.append(f"self_review attempt {attempt + 1} error: {exc}")
            continue

        if review["pass"]:
            db.update_lead(
                lead_id,
                draft_subject=review["revised_subject"],
                draft_body=review["revised_body"],
                draft_status="ready_to_send",
                self_review_notes=None,
            )
            return "ready_to_send"

        issue_text = "; ".join(review.get("issues") or [])
        notes.append(f"self_review attempt {attempt + 1}: {issue_text}")

        # On final attempt, store the model's revised version even if flagged
        if attempt == 1:
            db.update_lead(
                lead_id,
                draft_subject=review["revised_subject"],
                draft_body=review["revised_body"],
                draft_status="flagged",
                self_review_notes=" | ".join(notes),
            )
            final_status = "flagged"
            return final_status

    db.update_lead(
        lead_id,
        draft_status="flagged",
        self_review_notes=" | ".join(notes) if notes else "Draft pipeline failed",
    )
    return final_status


def scrape_lead_to_db(
    raw_lead: Dict[str, Any],
    conn,
    min_score: int,
) -> Optional[Dict[str, Any]]:
    """Insert scraped lead if new and above min_score. Returns DB row or None."""
    score = int(raw_lead.get("Score") or 0)
    if score < min_score:
        return None

    website = normalize_domain(raw_lead.get("Website") or "")
    email = (raw_lead.get("Email") or "").lower().strip()
    if db.lead_exists(website, email, conn=conn):
        return None

    lead_id = db.insert_lead({
        "company": raw_lead.get("Company", ""),
        "website": website,
        "email": email,
        "email_type": raw_lead.get("Email Type", ""),
        "score": score,
        "country": raw_lead.get("Country", ""),
        "page_context": raw_lead.get("page_context", ""),
        "draft_status": "none",
        "status": "pending",
    }, conn=conn)

    if lead_id is None:
        return None
    return db.get_lead(lead_id, conn=conn)


def run_worker(args: argparse.Namespace) -> None:
    logger = setup_logging()
    conn = db.get_connection()

    profile = db.get_profile()
    en_path, fr_path = resolve_cv_paths(profile)
    if not profile.get("bio"):
        logger.warning(
            "Bio is empty — open the Streamlit dashboard and fill in "
            "'Your Profile' before expecting quality drafts."
        )
    if not build_cv_text_for_llm(profile):
        logger.warning(
            "No CV PDF found — upload CVs in the dashboard or place files at:\n"
            "  %s\n  %s",
            cv_file_status(en_path),
            cv_file_status(fr_path),
        )

    stats = {
        "cycles": 0,
        "leads_found": 0,
        "ready": 0,
        "flagged": 0,
        "rejected": 0,
        "skipped_dup": 0,
    }
    start_time = time.time()

    logger.info(
        "Worker started — field=%r type=%r location=%r remote=%s min_score=%d interval=%ds",
        args.field, args.type, args.location, args.remote, args.min_score, args.cycle_interval,
    )

    try:
        while True:
            stats["cycles"] += 1
            cycle_start = datetime.now(timezone.utc).isoformat()
            logger.info("── Cycle %d started at %s ──", stats["cycles"], cycle_start)

            region = region_for_location(args.location)
            queries = build_search_queries(args.field, args.type, args.location, args.remote)

            def log_fn(msg: str) -> None:
                logger.debug(msg)

            found = search_and_scrape(queries, max_results=10, region=region, debug_fn=log_fn)
            logger.info("Scrape returned %d raw lead(s)", len(found))

            for raw in found:
                lead_row = scrape_lead_to_db(raw, conn, args.min_score)
                if lead_row is None:
                    stats["skipped_dup"] += 1
                    continue

                stats["leads_found"] += 1
                profile = db.get_profile(conn=conn)

                status = process_lead_draft(
                    profile, lead_row, args.field, args.type, logger
                )
                if status == "ready_to_send":
                    stats["ready"] += 1
                elif status == "flagged":
                    stats["flagged"] += 1
                elif status == "rejected":
                    stats["rejected"] += 1

                quality = lead_row.get("contact_quality") or "?"
                updated = db.get_lead(lead_row["id"], conn=conn)
                quality = (updated or {}).get("contact_quality") or quality
                logger.info(
                    "%s | %s | contact=%s | draft=%s",
                    lead_row.get("company", "?"),
                    lead_row.get("email", "?"),
                    quality,
                    status,
                )

            logger.info(
                "Cycle %d done — sleeping %ds (ready=%d flagged=%d rejected=%d total_found=%d)",
                stats["cycles"],
                args.cycle_interval,
                stats["ready"],
                stats["flagged"],
                stats["rejected"],
                stats["leads_found"],
            )
            time.sleep(args.cycle_interval)

    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        mins, secs = divmod(int(elapsed), 60)
        hours, mins = divmod(mins, 60)
        runtime = f"{hours}h {mins}m {secs}s" if hours else f"{mins}m {secs}s"

        summary = (
            f"\n{'=' * 50}\n"
            f"Worker stopped (Ctrl+C)\n"
            f"  Cycles run:     {stats['cycles']}\n"
            f"  Leads found:    {stats['leads_found']}\n"
            f"  Ready to send:  {stats['ready']}\n"
            f"  Flagged:        {stats['flagged']}\n"
            f"  Rejected:       {stats['rejected']}\n"
            f"  Skipped (dup):  {stats['skipped_dup']}\n"
            f"  Runtime:        {runtime}\n"
            f"{'=' * 50}"
        )
        logger.info(summary)
        print(summary)
        conn.close()
        sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoApply outreach worker")
    parser.add_argument("--field", required=True, help='Job field, e.g. "Data Science"')
    parser.add_argument(
        "--type",
        required=True,
        choices=["Internship", "Full-time job", "Spontaneous application"],
        help="Application type",
    )
    parser.add_argument("--location", default="", help="Optional location filter")
    parser.add_argument("--remote", action="store_true", help="Include remote companies")
    parser.add_argument(
        "--cycle-interval",
        type=int,
        default=900,
        help="Seconds between scrape cycles (default: 900)",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=40,
        help="Minimum email score to process (default: 40)",
    )
    args = parser.parse_args()
    run_worker(args)


if __name__ == "__main__":
    main()
