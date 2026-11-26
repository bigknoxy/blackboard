"""
Pure deterministic web-grounding, link-verification, and deliverable-categorization helpers.

Contains NO LLM calls and NO shell commands. Prevents hallucinated product URLs
(such as repeated fake Amazon ASINs) from leaking into Telegram and other channels
across any workflow.
"""

import re
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Tuple


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_RAW_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
_BARE_AMAZON_RE = re.compile(r"(?<![/\w])(?:www\.)?amazon\.[a-z.]+/(?:dp|gp/product)/[^\s<>\"')\]]+", re.IGNORECASE)


def extract_markdown_links(text: str) -> List[Tuple[str, str]]:
    """Extract all Markdown links [label](url) as (label, url) tuples."""
    links: List[Tuple[str, str]] = []
    for match in _MD_LINK_RE.finditer(text or ""):
        label = match.group(1).strip()
        raw_url = match.group(2).strip()
        title_match = re.search(r"\s+['\"](.+?)['\"]$", raw_url)
        url = raw_url[: title_match.start()].rstrip() if title_match else raw_url
        links.append((label, url))
    return links


def build_safe_search_url(label: str, domain_hint: Optional[str] = None) -> str:
    """
    Build a guaranteed-working search URL from a product/item label.
    Strips trailing ' on Amazon' / ' on Adafruit' and quote_plus-encodes the query.
    """
    raw = (label or "").strip()
    inferred_domain = domain_hint
    if inferred_domain is None:
        if re.search(r"\s+on\s+adafruit$", raw, flags=re.IGNORECASE):
            inferred_domain = "adafruit.com"
        else:
            inferred_domain = "amazon.com"

    clean = re.sub(r"\s+on\s+(?:amazon|adafruit)$", "", raw, flags=re.IGNORECASE).strip()
    if not clean:
        clean = "product"

    domain = inferred_domain.strip().lower()
    if domain.startswith("https://"):
        domain = domain[len("https://") :]
    elif domain.startswith("http://"):
        domain = domain[len("http://") :]
    if domain.startswith("www."):
        domain = domain[4:]
    domain = domain.split("/")[0] or "amazon.com"

    encoded = urllib.parse.quote_plus(clean)
    return f"https://www.{domain}/s?k={encoded}"


def _is_safe_search_url(url: str) -> bool:
    """Return True if URL is already a safe search query URL (/s?k= or /search?q=)."""
    return bool(
        re.match(r"^https?://[^\s/]+/(?:s\?k=|search\?q=)", url or "", re.IGNORECASE)
    )


def _normalize_url_key(url: str) -> str:
    """Normalize trailing punctuation and www prefix for verified set membership checks."""
    u = (url or "").strip().rstrip(".,;:")
    if not u.startswith(("http://", "https://")):
        u = f"https://{u}"
    return u


def _is_in_verified(url: str, verified_set: set) -> bool:
    norm = _normalize_url_key(url)
    if norm in verified_set or url.rstrip(".,;:") in verified_set:
        return True
    # Also match with/without www.
    alt = norm.replace("://www.", "://", 1) if "://www." in norm else norm.replace("://", "://www.", 1)
    return alt in verified_set


def sanitize_and_ground_links(
    text: str,
    verified_urls: Optional[Iterable[str]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Deterministically sanitize and ground URLs in text:
    - Verbatim URLs present in `verified_urls` are preserved untouched.
    - Valid search URLs (/s?k=, /search?q=) are preserved untouched.
    - Unverified direct product URLs (e.g. hallucinated amazon.com/dp/...) or
      duplicate unverified URLs are rewritten to distinct safe search URLs
      derived from the Markdown link label (or fallback product query).
    """
    verified_set = {_normalize_url_key(u) for u in (verified_urls or []) if u}
    verified_count = 0
    rewritten_count = 0
    unverified_counts: Dict[str, int] = {}

    # 1. Process Markdown links [label](url) first so each item gets a distinct label-based search URL
    def _replace_md(match: re.Match) -> str:
        nonlocal verified_count, rewritten_count
        label = match.group(1).strip()
        raw_url = match.group(2).strip()
        title_match = re.search(r"\s+['\"](.+?)['\"]$", raw_url)
        url = raw_url[: title_match.start()].rstrip() if title_match else raw_url

        if _is_in_verified(url, verified_set) or _is_safe_search_url(url):
            verified_count += 1
            return match.group(0)

        norm = _normalize_url_key(url)
        unverified_counts[norm] = unverified_counts.get(norm, 0) + 1
        rewritten_count += 1
        safe_url = build_safe_search_url(label)
        return f"[{label}]({safe_url})"

    working = _MD_LINK_RE.sub(_replace_md, text or "")

    # Track spans of Markdown links so raw-URL pass does not touch already-processed Markdown URLs
    def _md_spans(s: str) -> List[Tuple[int, int]]:
        return [(m.start(), m.end()) for m in _MD_LINK_RE.finditer(s)]

    def _in_spans(pos: int, spans: List[Tuple[int, int]]) -> bool:
        return any(a <= pos < b for a, b in spans)

    # 2. Process raw HTTP(S) URLs outside Markdown links
    md_spans = _md_spans(working)

    def _replace_raw(match: re.Match) -> str:
        nonlocal verified_count, rewritten_count
        if _in_spans(match.start(), md_spans):
            return match.group(0)
        raw_tok = match.group(0)
        trailing = ""
        while raw_tok and raw_tok[-1] in ".,;:!?":
            trailing = raw_tok[-1] + trailing
            raw_tok = raw_tok[:-1]
        if _is_in_verified(raw_tok, verified_set) or _is_safe_search_url(raw_tok):
            verified_count += 1
            return f"{raw_tok}{trailing}"

        norm = _normalize_url_key(raw_tok)
        unverified_counts[norm] = unverified_counts.get(norm, 0) + 1
        rewritten_count += 1
        # Use preceding context or product token for distinct query
        safe_url = build_safe_search_url(f"product {rewritten_count}")
        return f"{safe_url}{trailing}"

    working = _RAW_URL_RE.sub(_replace_raw, working)

    # 3. Process bare protocol-less amazon.com/dp/... URLs outside Markdown/HTTP URLs
    md_spans = _md_spans(working)
    http_spans = [(m.start(), m.end()) for m in _RAW_URL_RE.finditer(working)]

    def _replace_bare(match: re.Match) -> str:
        nonlocal verified_count, rewritten_count
        if _in_spans(match.start(), md_spans) or _in_spans(match.start(), http_spans):
            return match.group(0)
        raw_tok = match.group(0)
        trailing = ""
        while raw_tok and raw_tok[-1] in ".,;:!?":
            trailing = raw_tok[-1] + trailing
            raw_tok = raw_tok[:-1]
        if _is_in_verified(raw_tok, verified_set):
            verified_count += 1
            return f"{raw_tok}{trailing}"

        norm = _normalize_url_key(raw_tok)
        unverified_counts[norm] = unverified_counts.get(norm, 0) + 1
        rewritten_count += 1
        safe_url = build_safe_search_url(f"product {rewritten_count}")
        return f"{safe_url}{trailing}"

    working = _BARE_AMAZON_RE.sub(_replace_bare, working)

    duplicate_urls = [u for u, cnt in unverified_counts.items() if cnt > 1]
    report = {
        "verified_count": verified_count,
        "rewritten_count": rewritten_count,
        "duplicate_urls": duplicate_urls,
    }
    return working, report


def verify_deliverable_links(
    text: str,
    verified_urls: Optional[Iterable[str]] = None,
    require_links: bool = False,
) -> Dict[str, Any]:
    """
    Deterministic verification gate for web-grounded deliverables.
    Fails if any unverified/hallucinated URLs were detected (while still returning
    `sanitized_text` with safe rewritten URLs), or if `require_links=True` and no links exist.
    """
    sanitized_text, report = sanitize_and_ground_links(text, verified_urls=verified_urls)
    issues: List[str] = []

    if report["rewritten_count"] > 0:
        issues.append(
            f"Detected {report['rewritten_count']} unverified URL(s) not grounded in web_search results."
        )
    if report["duplicate_urls"]:
        issues.append(
            f"Detected duplicate unverified URL(s): {', '.join(report['duplicate_urls'][:3])}"
        )
    if require_links and report["verified_count"] == 0 and report["rewritten_count"] == 0:
        issues.append("No links found in deliverable despite require_links=True.")

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "sanitized_text": sanitized_text,
        "report": report,
    }


def categorize_deliverable(user_msg: str) -> str:
    """
    Deterministic keyword categorizer (used directly and as offline fallback for
    Concierge's toggleable Decision Model).
    Returns one of: shopping_list, code_review, troubleshooting, software_plan, general_research.
    """
    msg = (user_msg or "").strip().lower()
    if not msg:
        return "general_research"

    if any(
        kw in msg
        for kw in [
            "shopping",
            "buy",
            "amazon",
            "adafruit",
            "product link",
            "products",
            "price",
            "groceries",
            "parts list",
        ]
    ):
        return "shopping_list"

    if any(
        kw in msg
        for kw in [
            "code review",
            "review this pull request",
            "review pr",
            "pull request",
            "pr #",
            "check my code",
            "audit the code",
            "audit code",
        ]
    ):
        return "code_review"

    if any(
        kw in msg
        for kw in [
            "not turning on",
            "not working",
            "won't start",
            "troubleshoot",
            "debug",
            "fix the bug",
            "fix bug",
            "broken",
            "crash",
            "failing",
        ]
    ):
        return "troubleshooting"

    if any(
        kw in msg
        for kw in [
            "software plan",
            "architecture",
            "design doc",
            "roadmap",
            "enhancement plan",
            "implementation plan",
            "spec",
        ]
    ):
        return "software_plan"

    return "general_research"
