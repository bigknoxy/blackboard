"""
Unit tests for web_grounding.py deterministic helpers.

These tests verify link extraction, URL grounding/sanitization, and deliverable
categorization without any LLM or shell dependencies.
"""

import pytest

from app.web_grounding import (
    extract_markdown_links,
    build_safe_search_url,
    sanitize_and_ground_links,
    verify_deliverable_links,
    categorize_deliverable,
)


# ── extract_markdown_links ─────────────────────────────────────────────────────

def test_extract_markdown_links_basic():
    text = "Check out [Google](https://google.com) and [Bing](https://bing.com)."
    links = extract_markdown_links(text)
    assert links == [("Google", "https://google.com"), ("Bing", "https://bing.com")]


def test_extract_markdown_links_with_titles():
    text = "[Google](https://google.com 'Search engine')"
    links = extract_markdown_links(text)
    assert links == [("Google", "https://google.com")]


def test_extract_markdown_links_multiple():
    text = "[A](url1) [B](url2) [C](url3)"
    links = extract_markdown_links(text)
    assert len(links) == 3


# ── build_safe_search_url ─────────────────────────────────────────────────────

def test_build_safe_search_url_amazon():
    url = build_safe_search_url("usb cable")
    assert url == "https://www.amazon.com/s?k=usb+cable"


def test_build_safe_search_url_strips_amazon_suffix():
    url = build_safe_search_url("usb cable on Amazon")
    assert url == "https://www.amazon.com/s?k=usb+cable"


def test_build_safe_search_url_strips_adafruit_suffix():
    url = build_safe_search_url("arduino kit on Adafruit")
    assert url == "https://www.adafruit.com/s?k=arduino+kit"


def test_sanitize_seven_duplicate_amazon_markdown_links():
    text = "\n".join(
        [
            "- [NodeMCU-32S on Amazon](https://www.amazon.com/dp/B07Q58Y7Z3)",
            "- [INMP441 I2S Microphone on Amazon](https://www.amazon.com/dp/B07Q58Y7Z3)",
            "- [3W 4Ohm Speaker on Amazon](https://www.amazon.com/dp/B07Q58Y7Z3)",
            "- [18650 Battery Holder on Amazon](https://www.amazon.com/dp/B07Q58Y7Z3)",
            "- [LM2596 Module on Amazon](https://www.amazon.com/dp/B07Q58Y7Z3)",
            "- [Project Box Enclosure on Amazon](https://www.amazon.com/dp/B07Q58Y7Z3)",
            "- [Toggle Switch on Amazon](https://www.amazon.com/dp/B07Q58Y7Z3)",
        ]
    )
    sanitized, report = sanitize_and_ground_links(text, verified_urls=[])
    links = extract_markdown_links(sanitized)
    urls = [u for _, u in links]
    assert len(urls) == 7
    assert len(set(urls)) == 7  # 7 distinct search URLs!
    assert all(u.startswith("https://www.amazon.com/s?k=") for u in urls)
    assert "B07Q58Y7Z3" not in sanitized
    assert report["rewritten_count"] == 7
    assert "https://www.amazon.com/dp/B07Q58Y7Z3" in report["duplicate_urls"]


def test_build_safe_search_url_custom_domain():
    url = build_safe_search_url("test product", domain_hint="adafruit.com")
    assert url == "https://www.adafruit.com/s?k=test+product"


def test_build_safe_search_url_default():
    url = build_safe_search_url("widget")
    assert url.startswith("https://www.amazon.com/s?k=")


# ── sanitize_and_ground_links ─────────────────────────────────────────────────

def test_sanitize_and_ground_links_verified_preserved():
    text = "See https://www.amazon.com/dp/B07Q58Y7Z3 for details."
    sanitized, report = sanitize_and_ground_links(
        text, verified_urls=["https://www.amazon.com/dp/B07Q58Y7Z3"]
    )
    # Verified URL should be preserved verbatim
    assert "https://www.amazon.com/dp/B07Q58Y7Z3" in sanitized
    assert report["verified_count"] == 1
    assert report["rewritten_count"] == 0


def test_sanitize_and_ground_links_unverified_rewritten():
    text = "Check out https://www.amazon.com/dp/B07Q58Y7Z3"
    sanitized, report = sanitize_and_ground_links(
        text, verified_urls=[]
    )
    # Unverified Amazon DP URL should be rewritten to search URL
    assert "amazon.com/dp/" not in sanitized
    assert "amazon.com/s?k=" in sanitized
    assert report["rewritten_count"] >= 1


def test_sanitize_and_ground_links_duplicate_urls():
    text = (
        "Product A: https://www.amazon.com/dp/B07Q58Y7Z3. "
        "Product B: https://www.amazon.com/dp/B07Q58Y7Z3. "
        "Product C: https://www.example.com/product."
    )
    sanitized, report = sanitize_and_ground_links(
        text, verified_urls=[]
    )
    # The duplicate amazon.com/dp/ URLs should be rewritten to distinct search URLs
    assert "amazon.com/dp/" not in sanitized, "Unverified Amazon DP URLs should be grounded"
    assert "amazon.com/s?k=" in sanitized, "Unverified URLs should be rewritten to search URLs"
    # All 3 products should have been rewritten (verified_count = 0, rewritten_count = 3)
    assert report["rewritten_count"] == 3, f"Expected 3 rewritten URLs, got {report['rewritten_count']}"
    assert report["verified_count"] == 0, f"Expected 0 verified URLs, got {report['verified_count']}"
    # Check that no amazon.com/dp/ remains in the sanitized text
    from app.web_grounding import extract_markdown_links
    md_links = extract_markdown_links(sanitized)
    assert len([u for _, u in md_links if "amazon.com/dp/" in u]) == 0


def test_sanitize_and_ground_links_valid_search_preserved():
    text = "Results from prior search: https://www.amazon.com/s?k=test"
    sanitized, report = sanitize_and_ground_links(
        text, verified_urls=[]
    )
    # Valid search URLs should be preserved
    assert "https://www.amazon.com/s?k=test" in sanitized
    assert report["verified_count"] == 1


def test_sanitize_and_ground_links_mixed():
    text = (
        "Verified: https://www.amazon.com/dp/B07Q58Y7Z3 | "
        "Unverified: https://www.amazon.com/dp/B01M0G9TTP | "
        "Search: https://www.amazon.com/s?k=prior"
    )
    sanitized, report = sanitize_and_ground_links(
        text, verified_urls=["https://www.amazon.com/dp/B07Q58Y7Z3"]
    )
    # Verified preserved, unverified rewritten, search preserved
    assert "amazon.com/dp/B07Q58Y7Z3" in sanitized
    assert "amazon.com/s?k=" in sanitized
    assert "https://www.amazon.com/s?k=prior" in sanitized
    assert report["verified_count"] == 2  # 1 verified URL + 1 valid search URL


# ── verify_deliverable_links ──────────────────────────────────────────────────

def test_verify_deliverable_links_pass():
    text = "Check out this amazon.com/dp/B07Q58Y7Z3 product."
    result = verify_deliverable_links(
        text, verified_urls=["https://www.amazon.com/dp/B07Q58Y7Z3"]
    )
    assert result["passed"] is True
    assert len(result["issues"]) == 0


def test_verify_deliverable_links_fail_unverified():
    text = "Check out this amazon.com/dp/B01M0G9TTP product."
    result = verify_deliverable_links(
        text, verified_urls=["https://www.amazon.com/dp/B07Q58Y7Z3"]
    )
    assert result["passed"] is False
    assert len(result["issues"]) > 0


def test_verify_deliverable_links_no_require():
    text = "No links here."
    result = verify_deliverable_links(text, verified_urls=[], require_links=False)
    assert result["passed"] is True


def test_verify_deliverable_links_require_false_with_links():
    text = "Check this link: https://www.amazon.com/dp/B07Q58Y7Z3"
    result = verify_deliverable_links(text, verified_urls=[], require_links=False)
    assert "passed" in result


# ── categorize_deliverable ────────────────────────────────────────────────────

def test_categorize_deliverable_shopping_list():
    assert categorize_deliverable("I need to buy a new laptop on amazon") == "shopping_list"
    assert categorize_deliverable("Shopping for groceries") == "shopping_list"


def test_categorize_deliverable_software_plan():
    assert categorize_deliverable("I need a software plan for my project") == "software_plan"
    assert categorize_deliverable("Code review needed for PR #123") == "code_review"


def test_categorize_deliverable_code_review():
    assert categorize_deliverable("Please review this pull request") == "code_review"
    assert categorize_deliverable("Check my code for errors") == "code_review"


def test_categorize_deliverable_general_research():
    assert categorize_deliverable("Research on climate change effects") == "general_research"
    assert categorize_deliverable("Investigate the issue") == "general_research"


def test_categorize_deliverable_troubleshooting():
    assert categorize_deliverable("My computer is not turning on") == "troubleshooting"
    assert categorize_deliverable("Fix the bug in the code") == "troubleshooting"


def test_categorize_deliverable_default():
    assert categorize_deliverable("Just saying hello") == "general_research"
    assert categorize_deliverable("") == "general_research"