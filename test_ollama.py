#!/usr/bin/env python3
"""
Diagnose Ollama connectivity and time the real drafting pipeline.

Usage:
    python test_ollama.py                  # ping + classify/draft/review on a DB lead
    python test_ollama.py --lead-id 6      # specific lead
    python test_ollama.py --ping-only      # health check only
    python test_ollama.py --model ministral-3:3b
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

import db
import llm
from cv_utils import build_cv_text_for_llm


def _host() -> str:
    return os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def _model(override: Optional[str] = None) -> str:
    return override or os.getenv("OLLAMA_MODEL", "gemma3:4b-it-qat")


def check_stuck_runner() -> None:
    """Warn if an ollama runner is pegging CPU (usually a timed-out leftover job)."""
    try:
        import subprocess

        out = subprocess.check_output(
            ["ps", "-eo", "pid,%cpu,etime,cmd"], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if "ollama runner" not in line:
                continue
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            cpu = float(parts[1])
            if cpu > 50:
                print(
                    f"⚠ Ollama runner is busy (CPU {cpu:.0f}%, elapsed {parts[2]}).\n"
                    "  A previous timed-out request may still be generating and will\n"
                    "  block new ones. Clear it with:\n"
                    "    curl -s http://localhost:11434/api/generate "
                    "-d '{\"model\":\"gemma3:4b-it-qat\",\"keep_alive\":0}'\n"
                    "  or: sudo systemctl restart ollama\n"
                )
    except Exception:
        pass


def check_server() -> bool:
    print(f"Host:  {_host()}")
    print(f"Model: {_model()}")
    print(f"Timeout (OLLAMA_TIMEOUT): {os.getenv('OLLAMA_TIMEOUT', '30')}s")
    print()
    check_stuck_runner()
    try:
        r = requests.get(f"{_host()}/api/tags", timeout=5)
        r.raise_for_status()
        models = [m.get("name") for m in r.json().get("models", [])]
        print(f"✅ Ollama reachable — {len(models)} model(s):")
        for name in models:
            mark = " ← configured" if name == _model() else ""
            print(f"   • {name}{mark}")
        if _model() not in models:
            print(f"\n❌ Configured model {_model()!r} is NOT installed.")
            print(f"   Run: ollama pull {_model()}")
            return False
        return True
    except requests.RequestException as exc:
        print(f"❌ Cannot reach Ollama at {_host()}: {exc}")
        print("   Is `ollama serve` running?")
        return False


def timed_chat(label: str, system: str, user: str, num_predict: int = 256) -> dict:
    """Low-level timed /api/chat call with timing breakdown."""
    payload = {
        "model": _model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        # Thinking models (gemma4:e4b) otherwise burn num_predict on reasoning
        # and return empty content → JSON parse fail.
        "think": False,
        "keep_alive": "10m",
        "options": {
            "temperature": 0.2,
            "num_predict": num_predict,
        },
    }
    prompt_chars = len(system) + len(user)
    print(f"\n── {label} ──")
    print(f"prompt chars: {prompt_chars}  num_predict: {num_predict}")
    t0 = time.time()
    try:
        r = requests.post(
            f"{_host()}/api/chat",
            json=payload,
            timeout=(10, int(os.getenv("OLLAMA_TIMEOUT", "180"))),
        )
        wall = time.time() - t0
        r.raise_for_status()
        data = r.json()
        content = data.get("message", {}).get("content", "")
        print(f"wall time:   {wall:.1f}s")
        print(f"ollama total:{ (data.get('total_duration') or 0)/1e9 :.1f}s"
              f"  load:{(data.get('load_duration') or 0)/1e9:.1f}s"
              f"  prompt_eval:{data.get('prompt_eval_count')}"
              f"  eval:{data.get('eval_count')}"
              f"  tok/s:{(data.get('eval_count') or 0) / max((data.get('eval_duration') or 1)/1e9, 0.001):.1f}")
        print(f"raw JSON response:\n{content[:1200]}")
        if len(content) > 1200:
            print(f"... ({len(content)} chars total)")
        try:
            parsed = json.loads(content)
            print("parsed OK ✅")
            return {"ok": True, "wall": wall, "parsed": parsed, "raw": content}
        except json.JSONDecodeError as exc:
            print(f"JSON parse FAIL ❌: {exc}")
            return {"ok": False, "wall": wall, "parsed": None, "raw": content}
    except requests.Timeout:
        wall = time.time() - t0
        print(f"TIMEOUT after {wall:.1f}s ❌")
        return {"ok": False, "wall": wall, "parsed": None, "raw": None}
    except requests.RequestException as exc:
        wall = time.time() - t0
        print(f"ERROR after {wall:.1f}s ❌: {exc}")
        return {"ok": False, "wall": wall, "parsed": None, "raw": None}


def pick_lead(lead_id: Optional[int]) -> Optional[dict]:
    if lead_id:
        lead = db.get_lead(lead_id)
        if not lead:
            print(f"No lead with id={lead_id}")
            return None
        return lead
    leads = db.get_leads()
    if not leads:
        print("No leads in leads.db — run the worker once, or use the sample below.")
        return None
    # Prefer a flagged / high-score lead so the test matches real worker pain
    ranked = sorted(
        leads,
        key=lambda L: (
            0 if L.get("draft_status") == "flagged" else 1,
            -(L.get("score") or 0),
        ),
    )
    return ranked[0]


def sample_lead() -> dict:
    return {
        "id": 0,
        "company": "Conroar",
        "email": "info@conroar.com",
        "country": "International",
        "page_context": (
            "Hire Remote Data Scientists - Conroar\n"
            "Conroar helps companies hire remote data scientists and ML engineers. "
            "Roles listed include Data Scientist, ML Engineer, and Analytics."
        ),
    }


def run_pipeline(lead: dict, field: str, app_type: str) -> None:
    profile = db.get_profile()
    bio = profile.get("bio") or ""
    cv = build_cv_text_for_llm(profile)
    print("\n=== Profile / lead sizes ===")
    print(f"bio chars:          {len(bio)}")
    print(f"name:               {db.profile_full_name(profile)!r}")
    print(f"phone:              {(profile.get('phone') or '')!r}")
    print(f"cv_text (cached):   {len(profile.get('cv_text') or '')}")
    print(f"cv for LLM (full):  {len(cv)}")
    print(f"cv used in draft:   {len(cv[: llm.CV_CHARS])}")
    print(f"company:            {lead.get('company')}")
    print(f"email:              {lead.get('email')}")
    print(f"page_context chars: {len(lead.get('page_context') or '')}")
    print(f"draft_status:       {lead.get('draft_status')}")
    notes = lead.get("self_review_notes") or ""
    if notes:
        print(f"self_review_notes:  {notes[:300]}")

    if not bio and not cv:
        print("\n⚠ Profile bio/CV empty — drafts will be generic. Save profile in the dashboard first.")

    print("\n=== 1) llm.classify_contact ===")
    t0 = time.time()
    try:
        result = llm.classify_contact(
            lead.get("company", ""),
            lead.get("email", ""),
            lead.get("page_context") or "",
        )
        print(f"OK in {time.time()-t0:.1f}s → {json.dumps(result, indent=2)}")
    except Exception as exc:
        print(f"FAIL in {time.time()-t0:.1f}s → {exc}")
        return

    print("\n=== 2) llm.draft_email ===")
    t0 = time.time()
    try:
        draft = llm.draft_email(profile, lead, field, app_type)
        print(f"OK in {time.time()-t0:.1f}s")
        print(f"subject: {draft.get('subject')}")
        print(f"body ({llm.word_count(draft.get('body',''))} words):\n{draft.get('body')}")
    except Exception as exc:
        print(f"FAIL in {time.time()-t0:.1f}s → {exc}")
        return

    reject = llm.pre_filter_draft(
        draft["subject"],
        draft["body"],
        lead.get("company", ""),
        full_name=db.profile_full_name(profile),
        phone=profile.get("phone") or "",
    )
    print(f"\n=== pre-filter === {'PASS' if reject is None else 'REJECT: ' + reject}")

    print("\n=== 3) llm.self_review ===")
    t0 = time.time()
    try:
        review = llm.self_review(profile, lead, draft, field, app_type)
        print(f"OK in {time.time()-t0:.1f}s")
        print(f"pass:   {review.get('pass')}")
        print(f"issues: {review.get('issues')}")
        print(f"revised subject: {review.get('revised_subject')}")
        print(f"revised body ({llm.word_count(review.get('revised_body',''))} words):\n{review.get('revised_body')}")
    except Exception as exc:
        print(f"FAIL in {time.time()-t0:.1f}s → {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test Ollama for AutoApply drafting")
    parser.add_argument("--lead-id", type=int, default=None, help="Use this lead id from leads.db")
    parser.add_argument("--ping-only", action="store_true", help="Only check server + tiny JSON")
    parser.add_argument("--model", default=None, help="Override OLLAMA_MODEL for this run")
    parser.add_argument("--field", default="Data Science")
    parser.add_argument("--type", default="Full-time job", dest="app_type")
    parser.add_argument(
        "--raw-bench",
        action="store_true",
        help="Also run raw timed calls (tiny / classify-sized / draft-sized) before the pipeline",
    )
    args = parser.parse_args()

    if args.model:
        os.environ["OLLAMA_MODEL"] = args.model
        llm.OLLAMA_MODEL = args.model

    print("=" * 60)
    print("AutoApply — Ollama diagnostic")
    print("=" * 60)

    if not check_server():
        sys.exit(1)

    # Tiny ping
    tiny = timed_chat(
        "tiny JSON ping",
        "Reply with JSON only.",
        'Return {"ok": true, "msg": "pong"}',
        num_predict=32,
    )
    if not tiny["ok"]:
        print("\nTiny ping failed — fix Ollama before debugging the pipeline.")
        sys.exit(1)

    if args.ping_only:
        print("\nPing-only done.")
        return

    lead = pick_lead(args.lead_id) or sample_lead()
    print(f"\nUsing lead id={lead.get('id')} company={lead.get('company')!r}")

    if args.raw_bench:
        system = 'Respond JSON only: {"quality":"personal"|"generic"|"invalid","reason":"..."}'
        user = (
            f"Company: {lead.get('company')}\nEmail: {lead.get('email')}\n"
            f"Page context:\n{(lead.get('page_context') or '')[:800]}"
        )
        timed_chat("classify-sized raw", system, user, num_predict=128)

        profile = db.get_profile()
        bio = (profile.get("bio") or "")[:800]
        cv = build_cv_text_for_llm(profile)[: llm.CV_CHARS]
        system = 'Respond JSON only: {"subject":"...","body":"..."}'
        user = (
            f"Applicant bio:\n{bio}\n\nCV highlights:\n{cv}\n\n"
            f"Target field: {args.field}\nApplication type: {args.app_type}\n"
            f"Company: {lead.get('company')}\n"
            f"Page context:\n{(lead.get('page_context') or '')[:600]}"
        )
        timed_chat("draft-sized raw", system, user, num_predict=llm.DRAFT_NUM_PREDICT)

    run_pipeline(lead, args.field, args.app_type)
    print("\nDone.")


if __name__ == "__main__":
    main()
