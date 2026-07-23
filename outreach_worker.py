#!/usr/bin/env python3
"""
AutoApply outreach worker — continuously finds leads, drafts emails via local LLM,
self-reviews drafts, and queues ready ones in leads.db.

Preferred usage (profile-driven):
    python outreach_worker.py

The worker asks the local LLM to propose diversified search angles from your
bio/CV, then rotates through them across scrape cycles.

Manual override (skip planning):
    python outreach_worker.py --field "Data Science" --type "Full-time job" --remote

Plan only (print suggestions, don't scrape):
    python outreach_worker.py --plan-only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

import db
import llm
from cv_utils import build_cv_text_for_llm, cv_file_status, resolve_cv_paths
from scraper import build_search_queries, normalize_domain, search_and_scrape

load_dotenv()

LOG_FILE = "worker.log"
PLAN_FILE = "search_plan.json"


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


def save_search_plan(plan: Dict[str, Any], path: str = PLAN_FILE) -> None:
    payload = {
        **plan,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "model": os.getenv("OLLAMA_MODEL", ""),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_search_plan(path: str = PLAN_FILE) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("searches"):
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def format_plan(plan: Dict[str, Any]) -> str:
    lines = ["Search plan:"]
    summary = (plan.get("summary") or "").strip()
    if summary:
        lines.append(f"  {summary}")
    for i, s in enumerate(plan.get("searches") or [], 1):
        remote = "remote" if s.get("remote") else "onsite/hybrid ok"
        loc = s.get("location") or "anywhere"
        lines.append(
            f"  {i}. [{s.get('app_type')}] {s.get('field')} "
            f"({loc}, {remote})"
        )
        if s.get("why"):
            lines.append(f"      why: {s['why']}")
    return "\n".join(lines)


def build_searches_from_args(args: argparse.Namespace) -> List[Dict[str, Any]]:
    return [{
        "field": args.field.strip(),
        "app_type": args.type,
        "location": (args.location or "").strip(),
        "remote": bool(args.remote),
        "why": "Manual CLI override",
    }]


def resolve_search_plan(
    args: argparse.Namespace,
    profile: Dict[str, str],
    logger: logging.Logger,
) -> List[Dict[str, Any]]:
    """
    Decide which searches to run:
      1. Explicit --field/--type → single manual search
      2. --reuse-plan + search_plan.json → reuse last plan
      3. Else → ask LLM to propose angles from profile
    """
    if args.field and args.type:
        logger.info("Using manual CLI search override")
        searches = build_searches_from_args(args)
        plan = {"summary": "Manual override from CLI flags.", "searches": searches}
        save_search_plan(plan)
        logger.info("\n%s", format_plan(plan))
        return searches

    if args.reuse_plan:
        plan = load_search_plan()
        if plan:
            logger.info("Reusing saved plan from %s", PLAN_FILE)
            logger.info("\n%s", format_plan(plan))
            return list(plan["searches"])
        logger.warning("No usable %s — generating a new plan", PLAN_FILE)

    logger.info("Asking local LLM for diversified search angles from your profile…")
    plan = llm.suggest_search_plan(profile)
    save_search_plan(plan)
    logger.info("Saved plan → %s", PLAN_FILE)
    logger.info("\n%s", format_plan(plan))
    return list(plan["searches"])


def run_one_search(
    search: Dict[str, Any],
    *,
    conn,
    profile: Dict[str, str],
    min_score: int,
    max_results: int,
    stats: Dict[str, int],
    logger: logging.Logger,
) -> None:
    field = search["field"]
    app_type = search["app_type"]
    location = search.get("location") or ""
    remote = bool(search.get("remote"))

    logger.info(
        "Searching: field=%r type=%r location=%r remote=%s",
        field, app_type, location or "(any)", remote,
    )
    region = region_for_location(location)
    queries = build_search_queries(field, app_type, location, remote)

    def log_fn(msg: str) -> None:
        logger.debug(msg)

    found = search_and_scrape(queries, max_results=max_results, region=region, debug_fn=log_fn)
    logger.info("Scrape returned %d raw lead(s) for %r", len(found), field)

    for raw in found:
        lead_row = scrape_lead_to_db(raw, conn, min_score)
        if lead_row is None:
            stats["skipped_dup"] += 1
            continue

        stats["leads_found"] += 1
        fresh_profile = db.get_profile(conn=conn)

        status = process_lead_draft(fresh_profile, lead_row, field, app_type, logger)
        if status == "ready_to_send":
            stats["ready"] += 1
        elif status == "flagged":
            stats["flagged"] += 1
        elif status == "rejected":
            stats["rejected"] += 1

        updated = db.get_lead(lead_row["id"], conn=conn)
        quality = (updated or {}).get("contact_quality") or lead_row.get("contact_quality") or "?"
        logger.info(
            "%s | %s | via=%r | contact=%s | draft=%s",
            lead_row.get("company", "?"),
            lead_row.get("email", "?"),
            field,
            quality,
            status,
        )


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

    searches = resolve_search_plan(args, profile, logger)

    if args.plan_only:
        logger.info("Plan-only mode — exiting without scraping.")
        conn.close()
        return

    stats = {
        "cycles": 0,
        "leads_found": 0,
        "ready": 0,
        "flagged": 0,
        "rejected": 0,
        "skipped_dup": 0,
    }
    start_time = time.time()
    search_index = 0

    logger.info(
        "Worker started — %d search angle(s), min_score=%d, interval=%ds, "
        "searches_per_cycle=%d",
        len(searches),
        args.min_score,
        args.cycle_interval,
        args.searches_per_cycle,
    )

    try:
        while True:
            stats["cycles"] += 1
            cycle_start = datetime.now(timezone.utc).isoformat()
            logger.info("── Cycle %d started at %s ──", stats["cycles"], cycle_start)

            # Rotate through plan: each cycle runs N angles, then advances
            n = max(1, min(args.searches_per_cycle, len(searches)))
            batch = []
            for _ in range(n):
                batch.append(searches[search_index % len(searches)])
                search_index += 1

            for search in batch:
                run_one_search(
                    search,
                    conn=conn,
                    profile=profile,
                    min_score=args.min_score,
                    max_results=args.max_results,
                    stats=stats,
                    logger=logger,
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
    parser = argparse.ArgumentParser(
        description="AutoApply outreach worker (profile-driven search by default)",
    )
    parser.add_argument(
        "--field",
        default=None,
        help='Optional override, e.g. "Data Science". If omitted, LLM plans searches from profile.',
    )
    parser.add_argument(
        "--type",
        default=None,
        choices=["Internship", "Full-time job", "Spontaneous application"],
        help="Optional application-type override (requires --field)",
    )
    parser.add_argument("--location", default="", help="Optional location filter (manual mode)")
    parser.add_argument("--remote", action="store_true", help="Include remote (manual mode)")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Generate/print search plan and exit (no scraping)",
    )
    parser.add_argument(
        "--reuse-plan",
        action="store_true",
        help=f"Reuse {PLAN_FILE} instead of regenerating with the LLM",
    )
    parser.add_argument(
        "--searches-per-cycle",
        type=int,
        default=1,
        help="How many search angles to run each cycle (default: 1, rotates through the plan)",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=10,
        help="Max search results per query backend (default: 10)",
    )
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

    if (args.field and not args.type) or (args.type and not args.field):
        parser.error("Use both --field and --type together, or neither for auto-planning.")

    run_worker(args)


if __name__ == "__main__":
    main()
