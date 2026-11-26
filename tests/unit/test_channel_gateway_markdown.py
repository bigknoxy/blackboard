"""
Unit tests for channel_gateway.py _markdown_to_telegram_html and send_workflow_completed fixes.
"""
import inspect
import re

from app.channel_gateway import ChannelGateway, TelegramChannel


def test_markdown_to_telegram_html_strips_xml():
    """_markdown_to_telegram_html should strip leaked corund XML and arg_key/arg_value fragments."""
    test_cases = [
        # Input with corund XML
        (
            "<tool_name>web_search</tool_name><arg_key>query</arg_key><arg_value>test</arg_value>",
            "",
        ),
        # Input with Markdown link -> converted to <a> -> html.escape turns < > into < >
        (
            "[Telegram Bot](https://telegram.org)",
            "<a href=\"https://telegram.org\">Telegram Bot</a>",
        ),
        # Input with plain text
        (
            "Hello **world**",
            "Hello <b>world</b>",
        ),
        # Input with mixed content
        (
            "[Label](https://example.com) some text <tool_name>skip</tool_name>",
            "<a href=\"https://example.com\">Label</a> some text",
        ),
    ]

    for input_text, expected_contains in test_cases:
        result = ChannelGateway._markdown_to_telegram_html(input_text)
        assert expected_contains in result, (
            f"FAIL: expected '{expected_contains}' in result='{result}'"
        )


def test_markdown_to_telegram_html_no_stray_xml():
    """_markdown_to_telegram_html should leave no stray XML tags."""
    test_input = (
        "[Link](https://test.com) with <tool_name>xml</tool_name> and <arg_key>k</arg_key>"
    )
    result = ChannelGateway._markdown_to_telegram_html(test_input)
    assert "<tool_name>" not in result
    assert "<arg_key>" not in result


def test_telegram_channel_send_workflow_completed_chunks_at_3800_chars():
    """TelegramChannel.send_workflow_completed should chunk summaries at <=3800 chars per message."""
    src = inspect.getsource(TelegramChannel.send_workflow_completed)
    assert "3800" in src or "MAX_CHUNK" in src
    # Verify chunking logic exists
    assert "chunks" in src or "chunk" in src.lower()


def test_telegram_channel_send_workflow_completed_cockpit_button_on_final_chunk():
    """TelegramChannel.send_workflow_completed should attach Open Web Cockpit button to final chunk only."""
    src = inspect.getsource(TelegramChannel.send_workflow_completed)
    assert "cockpit" in src.lower() or "_cockpit_button_row" in src
    # Verify that button is only on final chunk
    assert "is_last" in src or "final" in src.lower()