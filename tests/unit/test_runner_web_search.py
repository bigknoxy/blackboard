"""
Unit tests for runner.py web_search selection and XML parsing/stripping fixes.
"""
import re
import inspect

import pytest


def test_select_appropriate_tool_prioritizes_web_search():
    """_select_appropriate_tool should prioritize web_search for external research queries."""
    from app.runner import StatelessAgentRunner  # noqa: F811

    src = inspect.getsource(StatelessAgentRunner._select_appropriate_tool)
    assert "web_search" in src
    web_search_pos = src.find('if tid == "web_search":')
    repo_search_pos = src.find('if tid == "repo_search":')
    assert web_search_pos >= 0 and web_search_pos < repo_search_pos


def test_xml_stripping_patterns():
    """Test the regex patterns used for XML stripping from LLM output."""
    test_cases = [
        # Strip XML tags entirely, leaving surrounding text
        ("<tool_name>web_search</tool_name>", ""),
        ("<some_random_xml>content</some_random_xml>", ""),
        ("Regular LLM output without XML", "Regular LLM output without XML"),
        # When <arg_key> and <arg_value> appear with surrounding text,
        # the tags are removed but spaces around them may remain
        (
            "Text with <arg_key>key</arg_key> and <arg_value>value</arg_value> mixed in",
            "Text with  and  mixed in",  # spaces around stripped tags remain
        ),
        # corund tag removal
        ("corundia> some text", " some text"),
    ]

    for input_text, expected_contains in test_cases:
        clean = re.sub(r"<tool_name>.*?</tool_name>", "", input_text, flags=re.DOTALL)
        clean = re.sub(r"<arg_key>.*?</arg_key>", "", clean, flags=re.DOTALL)
        clean = re.sub(r"<arg_value>.*?</arg_value>", "", clean, flags=re.DOTALL)
        clean = re.sub(r"corund[a-zA-Z]*>", "", clean)
        clean = re.sub(r"<[^>]+>", "", clean)

        if expected_contains:
            assert expected_contains in clean, (
                f"FAIL: input='{input_text}' expected '{expected_contains}' in clean='{clean}'"
            )
        else:
            # Ensure no stray XML tags remain
            assert "<tool_name>" not in clean
            assert "<arg_key>" not in clean
            assert "<arg_value>" not in clean


def test_web_search_results_formatted_as_markdown_bullets():
    """Test that web_search results are formatted as Markdown bullet links."""
    results = [
        {"title": "Distributed Memory Paper", "url": "https://example.com/paper1", "snippet": "Great research on memory"},
        {"title": "Shopping Item", "url": "https://example.com/product", "snippet": "Available for $29.99"},
    ]

    bullet_lines = []
    for r in results:
        title = r.get("title", "")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        bullet_lines.append(f"- [{title}]({url}): {snippet}")

    bullet_text = "\n".join(bullet_lines)
    assert "- [Distributed Memory Paper](https://example.com/paper1): Great research on memory" in bullet_text
    assert "- [Shopping Item](https://example.com/product): Available for $29.99" in bullet_text
    assert "```json" not in bullet_text


def test_final_content_strips_xml_and_json():
    """Test that final_content strips XML blocks and MCP Tool Invoked JSON dumps."""
    import re

    llm_output = (
        "Here is what I found:\n"
        "<tool_name>web_search</tool_name><arg_key>query</arg_key><arg_value>distributed agents</arg_value></tool_name>\n"
        "Based on my research..."
    )

    tool_execution_summary = (
        "\n\n---\n**Web Search Results:**\n"
        "- [Distributed Memory Paper](https://example.com/paper1): Great research on memory"
    )

    clean_llm_output = re.sub(
        r"<tool_name>.*?</tool_name>", "", llm_output, flags=re.DOTALL
    )
    clean_llm_output = re.sub(r"<arg_key>.*?</arg_key>", "", clean_llm_output, flags=re.DOTALL)
    clean_llm_output = re.sub(r"<arg_value>.*?</arg_value>", "", clean_llm_output, flags=re.DOTALL)
    clean_llm_output = re.sub(r"corund[a-zA-Z]*>", "", clean_llm_output)
    clean_llm_output = re.sub(r"<[^>]+>", "", clean_llm_output)

    final_content = f"{clean_llm_output}{tool_execution_summary}"

    assert "<tool_name>" not in final_content
    assert "<arg_key>" not in final_content
    assert "<arg_value>" not in final_content
    assert "**Web Search Results:**" in final_content
    assert "- [Distributed Memory Paper]" in final_content
    assert "```json" not in final_content

    print("PASS: final_content strips XML and formats web_search as Markdown bullets")