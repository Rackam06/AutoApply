"""
CV file handling — uploads, paths, and PDF text extraction for LLM drafting.
"""

import os
from typing import Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

DEFAULT_CV_EN = os.getenv("CV_FILE_EN", "docs/CV_English.pdf")
DEFAULT_CV_FR = os.getenv("CV_FILE_FR", "docs/CV_French.pdf")


def ensure_docs_dir() -> None:
    os.makedirs("docs", exist_ok=True)


def resolve_cv_paths(profile: dict) -> Tuple[str, str]:
    """Return (english_path, french_path) from profile or env defaults."""
    en = (profile.get("cv_file_en") or "").strip() or DEFAULT_CV_EN
    fr = (profile.get("cv_file_fr") or "").strip() or DEFAULT_CV_FR
    return en, fr


def cv_for_country(profile: dict, country: str) -> str:
    """Pick the CV attachment path for a lead's country."""
    en, fr = resolve_cv_paths(profile)
    return fr if str(country).lower() == "france" else en


def cv_file_status(path: str) -> str:
    if os.path.exists(path):
        size_kb = os.path.getsize(path) // 1024
        return f"✅ {path} ({size_kb} KB)"
    return f"❌ {path} (not found — upload below or add manually to docs/)"


def extract_pdf_text(path: str, max_chars: int = 4000) -> str:
    """Extract text from a PDF for LLM context. Returns empty string on failure."""
    if not path or not os.path.exists(path):
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        parts = []
        for page in reader.pages[:6]:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts)[:max_chars]
    except Exception:
        return ""


def build_cv_text_for_llm(profile: dict) -> str:
    """
    Text derived from CV PDFs for drafting. Uses cached cv_text if present,
    otherwise extracts from the configured file paths.
    """
    cached = (profile.get("cv_text") or "").strip()
    if cached:
        return cached

    en, fr = resolve_cv_paths(profile)
    chunks = []
    en_text = extract_pdf_text(en)
    if en_text:
        chunks.append(f"[English CV]\n{en_text}")
    fr_text = extract_pdf_text(fr)
    if fr_text and fr_text != en_text:
        chunks.append(f"[French CV]\n{fr_text}")
    return "\n\n".join(chunks)[:4000]


def save_cv_upload(upload_bytes: bytes, dest_path: str) -> str:
    """Write uploaded CV bytes to dest_path, creating parent dirs."""
    ensure_docs_dir()
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(upload_bytes)
    return dest_path
