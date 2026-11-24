"""
Popperian Falsification 17: Interactive Telegram Gateway, Inline HITL Decisions & Channel Abstraction (SOLID).

Invariants:
1. Interactive Telegram Inbound Commands (Falsifier 17.1):
   Telegram webhook MUST parse standard commands (/status, /cockpit, /approvals, /steer)
   and natural language queries, routing through Concierge and returning formatted replies.
2. Interactive Inline Approval Callbacks (Falsifier 17.2):
   When an operator taps an inline button on Telegram ([Approve Once] or [Always Allow Tool]),
   the Telegram callback_query MUST atomically transition the ticket to APPROVED in PostgreSQL,
   create an auto-approval policy if requested, answer the callback query, edit the message text,
   and dispatch the pending tool execution.
3. Interactive Inline Rejection Callbacks (Falsifier 17.3):
   When an operator taps [Reject], the callback_query MUST transition the ticket to REJECTED,
   update the Telegram card, and post an audit record without executing the tool.
4. Channel Gateway Abstraction & Notifications (Falsifier 17.4):
   ChannelGateway MUST serve as the unified, SOLID abstraction for all channel operations.
   Pending HITL gate notifications MUST inject InlineKeyboardMarkup with inline buttons
   and WebApp links for instant 1-tap mobile decision-making.
"""

import os
import pytest
import uuid
import json
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete
from app.main import app
from app.database import get_db, get_redis
from app.models import HITLTicketModel, ApprovalPolicyModel, AgentModel, BlackboardEntryModel
from app.channel_gateway import channel_gateway


def create_mock_httpx_client():
    mock_instance = AsyncMock()
    mock_instance.__aenter__.return_value = mock_instance
    mock_instance.__aexit__.return_value = None
    mock_instance.post.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"ok": True}))
    return mock_instance


@pytest.fixture(autouse=True)
async def cleanup_telegram_redis():
    redis = await get_redis()
    prev_tok = await redis.get("channel:telegram:token")
    prev_chat = await redis.get("channel:telegram:chat_id")
    prev_en = await redis.get("channel:telegram:enabled")
    if prev_tok and prev_tok.startswith("mock-"):
        prev_tok = os.getenv("TELEGRAM_BOT_TOKEN")
    if prev_chat and prev_chat in ("987654321", "mock"):
        prev_chat = os.getenv("TELEGRAM_CHAT_ID")
    yield
    redis = await get_redis()
    env_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    env_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    target_tok = prev_tok or env_token
    target_chat = prev_chat or env_chat
    if target_tok and not target_tok.startswith("mock-"):
        await redis.set("channel:telegram:token", target_tok)
    else:
        await redis.delete("channel:telegram:token")
    if target_chat and target_chat not in ("987654321", "mock"):
        await redis.set("channel:telegram:chat_id", target_chat)
    else:
        await redis.delete("channel:telegram:chat_id")
    if prev_en is not None:
        await redis.set("channel:telegram:enabled", prev_en)
    else:
        await redis.set("channel:telegram:enabled", "true")


@pytest.mark.asyncio
async def test_falsifier_17_telegram_inbound_commands_and_concierge(db_session):
    """Falsifier 17.1: Telegram webhook processes /status, /cockpit, and conversational messages."""
    app.dependency_overrides[get_db] = lambda: db_session
    redis = await get_redis()
    await redis.set("channel:telegram:token", "mock-token-123")
    await redis.set("channel:telegram:chat_id", "987654321")
    await redis.set("channel:telegram:enabled", "true")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Test /status command
        mock_client = create_mock_httpx_client()
        with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
            status_payload = {
                "update_id": 1001,
                "message": {
                    "message_id": 42,
                    "from": {"id": 111, "username": "operator_dan"},
                    "chat": {"id": 987654321, "type": "private"},
                    "text": "/status",
                },
            }
            res = await client.post("/api/v1/channels/telegram/webhook", json=status_payload)
            assert res.status_code == 200
            assert res.json().get("status") == "ok"

            # Verify Telegram sendMessage was called with platform status
            mock_client.post.assert_called()
            sent_url = mock_client.post.call_args[0][0]
            sent_json = mock_client.post.call_args[1]["json"]
            assert "sendMessage" in sent_url
            assert sent_json["chat_id"] == "987654321"
            assert "Multi-Agent Blackboard Status" in sent_json["text"]
            assert "Agents" in sent_json["text"]

        # 2. Test /cockpit command (launches WebApp button)
        mock_client = create_mock_httpx_client()
        with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
            cockpit_payload = {
                "update_id": 1002,
                "message": {
                    "message_id": 43,
                    "from": {"id": 111, "username": "operator_dan"},
                    "chat": {"id": 987654321, "type": "private"},
                    "text": "/cockpit",
                },
            }
            res = await client.post("/api/v1/channels/telegram/webhook", json=cockpit_payload)
            assert res.status_code == 200

            mock_client.post.assert_called()
            sent_json = mock_client.post.call_args[1]["json"]
            assert "reply_markup" in sent_json
            assert "web_app" in str(sent_json["reply_markup"]) or "url" in str(sent_json["reply_markup"])

        # 3. Test conversational text routed to Concierge
        mock_client = create_mock_httpx_client()
        with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
            chat_payload = {
                "update_id": 1003,
                "message": {
                    "message_id": 44,
                    "from": {"id": 111, "username": "operator_dan"},
                    "chat": {"id": 987654321, "type": "private"},
                    "text": "What time is it?",
                },
            }
            res = await client.post("/api/v1/channels/telegram/webhook", json=chat_payload)
            assert res.status_code == 200

            mock_client.post.assert_called()
            # Verify sendChatAction ("typing") was called
            chat_action_calls = [
                c[1]["json"]
                for c in mock_client.post.call_args_list
                if "sendChatAction" in str(c[0])
            ]
            assert len(chat_action_calls) >= 1
            assert chat_action_calls[0]["action"] == "typing"
            assert str(chat_action_calls[0]["chat_id"]) == "987654321"

            # Verify message was sent
            msg_calls = [
                c[1]["json"]
                for c in mock_client.post.call_args_list
                if "sendMessage" in str(c[0])
            ]
            assert len(msg_calls) >= 1
            assert "chat_id" in msg_calls[0]
            assert len(msg_calls[0]["text"]) > 0


@pytest.mark.asyncio
async def test_falsifier_17_telegram_inline_approval_callback(db_session):
    """Falsifier 17.2: Telegram inline callback approves ticket, executes tool, and graduates trust policy."""
    app.dependency_overrides[get_db] = lambda: db_session
    redis = await get_redis()
    await redis.set("channel:telegram:token", "mock-token-123")
    await redis.set("channel:telegram:chat_id", "987654321")

    # Clean up test tool policy
    await db_session.execute(
        delete(ApprovalPolicyModel).where(
            ApprovalPolicyModel.tool_id == "git_branch_commit"
        )
    )
    await db_session.commit()

    # Create a pending ticket
    ticket_id = uuid.uuid4()
    ticket = HITLTicketModel(
        id=ticket_id,
        workflow_id="code_review_pipeline",
        task_id="task-tg-1",
        agent_id="coder",
        gate_type="tool_approval",
        tool_id="git_branch_commit",
        tool_arguments={"branch": "feat/tg-bot", "message": "Add telegram interactive approvals"},
        status="PENDING",
    )
    db_session.add(ticket)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        callback_payload = {
            "update_id": 2001,
            "callback_query": {
                "id": "cb_query_999",
                "from": {"id": 111, "username": "operator_dan"},
                "message": {
                    "message_id": 77,
                    "chat": {"id": 987654321},
                    "text": "🛡️ [Blackboard Tool HITL] Agent coder requested git_branch_commit",
                },
                "data": f"hitl:{ticket_id}:APPROVED:tool",
            },
        }

        mock_client = create_mock_httpx_client()
        with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
            res = await client.post("/api/v1/channels/telegram/webhook", json=callback_payload)
            assert res.status_code == 200
            assert res.json().get("status") in ("ok", "RESOLVED")

            # 1. Verify answerCallbackQuery was called
            calls = [c[0][0] for c in mock_client.post.call_args_list]
            assert any("answerCallbackQuery" in c for c in calls)

            # 2. Verify editMessageText was called
            assert any("editMessageText" in c for c in calls)

        # 3. Verify ticket status updated in PostgreSQL
        updated_ticket = await db_session.get(HITLTicketModel, ticket_id)
        assert updated_ticket is not None
        assert updated_ticket.status == "APPROVED"
        assert updated_ticket.reviewer_id == "@operator_dan"

        # 4. Verify auto-approval policy was created in DB
        stmt = select(ApprovalPolicyModel).where(
            ApprovalPolicyModel.tool_id == "git_branch_commit"
        )
        res_pol = await db_session.execute(stmt)
        policy = res_pol.scalars().first()
        assert policy is not None
        assert policy.policy_type == "tool"
        assert policy.tool_id == "git_branch_commit"

        # Clean up policy
        await db_session.delete(policy)
        await db_session.commit()


@pytest.mark.asyncio
async def test_falsifier_17_telegram_inline_rejection_callback(db_session):
    """Falsifier 17.3: Telegram inline callback rejects ticket and updates card without executing tool."""
    app.dependency_overrides[get_db] = lambda: db_session

    ticket_id = uuid.uuid4()
    ticket = HITLTicketModel(
        id=ticket_id,
        workflow_id="code_review_pipeline",
        task_id="task-tg-rej",
        agent_id="coder",
        gate_type="tool_approval",
        tool_id="bash_executor",
        tool_arguments={"command": "rm -rf /untrusted/dir"},
        status="PENDING",
    )
    db_session.add(ticket)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        callback_payload = {
            "update_id": 2002,
            "callback_query": {
                "id": "cb_query_rej_1",
                "from": {"id": 111, "username": "sec_operator"},
                "message": {
                    "message_id": 78,
                    "chat": {"id": 987654321},
                    "text": "🛡️ [Blackboard Tool HITL] Agent coder requested bash_executor",
                },
                "data": f"hitl:{ticket_id}:REJECTED:once",
            },
        }

        mock_client = create_mock_httpx_client()
        with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
            res = await client.post("/api/v1/channels/telegram/webhook", json=callback_payload)
            assert res.status_code == 200
            assert res.json().get("status") in ("ok", "RESOLVED")

        # Verify ticket status updated to REJECTED
        updated = await db_session.get(HITLTicketModel, ticket_id)
        assert updated is not None
        assert updated.status == "REJECTED"
        assert updated.reviewer_id == "@sec_operator"


@pytest.mark.asyncio
async def test_falsifier_17_channel_gateway_abstraction_and_notification(db_session):
    """Falsifier 17.4: ChannelGateway creates inline keyboard markup with Approve/Reject/WebApp buttons."""
    redis = await get_redis()
    await redis.set("channel:telegram:token", "mock-token-123")
    await redis.set("channel:telegram:chat_id", "987654321")
    await redis.set("channel:telegram:enabled", "true")

    ticket = HITLTicketModel(
        id=uuid.uuid4(),
        workflow_id="code_review_pipeline",
        task_id="task-notif-1",
        agent_id="sec-auditor",
        gate_type="tool_approval",
        tool_id="production_release_gate",
        tool_arguments={"release_tag": "v2.5.0"},
        status="PENDING",
    )

    mock_client = create_mock_httpx_client()
    with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
        res = await channel_gateway.notify_hitl_ticket(ticket)
        assert res is not None

        # Verify Telegram post had inline_keyboard
        tg_calls = [
            c[1]["json"]
            for c in mock_client.post.call_args_list
            if "api.telegram.org" in c[0][0]
        ]
        assert len(tg_calls) >= 1
        markup = tg_calls[0].get("reply_markup", {})
        keyboard = markup.get("inline_keyboard", [])
        assert len(keyboard) >= 1
        # Check for Approve Once, Always Allow, Reject buttons
        btn_texts = [b["text"] for row in keyboard for b in row]
        assert any("Approve" in t for t in btn_texts)
        assert any("Reject" in t for t in btn_texts)


@pytest.mark.asyncio
async def test_falsifier_17_telegram_tool_gap_analysis_and_xml_sanitization(db_session):
    """Falsifier 17.5: Concierge parses and strips raw <tool_call> XML tags and executes architect gap analysis."""
    app.dependency_overrides[get_db] = lambda: db_session
    redis = await get_redis()
    await redis.set("channel:telegram:token", "mock-token-123")
    await redis.set("channel:telegram:chat_id", "987654321")

    # 1. Verify static sanitizer strips raw XML tags and extracts tool calls
    from app.concierge import concierge_agent
    raw_llm_text = (
        'Here is the platform status:\n'
        '<tool_call>bash_executor<arg_key>command</arg_key><arg_value>echo "Analyzing tool inventory"</arg_value></tool_call>\n'
        'All tools evaluated.'
    )
    cleaned, extracted = concierge_agent._sanitize_and_extract_tool_calls(raw_llm_text)
    assert "<tool_call>" not in cleaned
    assert "</tool_call>" not in cleaned
    assert "<arg_key>" not in cleaned
    assert "echo \"Analyzing tool inventory\"" not in cleaned
    assert len(extracted) == 1
    assert extracted[0]["tool"] == "bash_executor"
    assert extracted[0]["arguments"]["command"] == 'echo "Analyzing tool inventory"'

    # 2. Test operator delegation request via Telegram
    mock_client = create_mock_httpx_client()
    with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            chat_payload = {
                "update_id": 1005,
                "message": {
                    "message_id": 46,
                    "from": {"id": 111, "username": "bigknoxy777"},
                    "chat": {"id": 987654321, "type": "private"},
                    "text": "Use the architect agent to figure out what tools we are missing based on current usage and best practices and what would benefit us the most",
                },
            }
            res = await client.post("/api/v1/channels/telegram/webhook", json=chat_payload)
            assert res.status_code == 200

            mock_client.post.assert_called()
            sent_calls = [
                c[1]["json"]
                for c in mock_client.post.call_args_list
                if "sendMessage" in c[0][0]
            ]
            assert len(sent_calls) >= 1
            sent_text = sent_calls[0].get("text", "")

            # Ensure no XML tags leaked
            assert "<tool_call>" not in sent_text
            assert "</tool_call>" not in sent_text
            assert "<arg_key>" not in sent_text
            assert "<arg_value>" not in sent_text

            # Ensure Architect recommendations are presented
            assert "Architect" in sent_text or "Tool Gap" in sent_text
            assert any(t in sent_text for t in ["code_linter_ast", "git_diff_inspector", "container_sandbox_runner"])

    # 3. Verify entry was posted to Blackboard by architect
    from app.models import BlackboardEntryModel
    arch_entries = (
        await db_session.execute(
            select(BlackboardEntryModel).where(
                BlackboardEntryModel.agent_id == "architect",
                BlackboardEntryModel.entry_type == "architecture_decision",
            )
        )
    ).scalars().all()
    assert len(arch_entries) >= 1
    assert "code_linter_ast" in arch_entries[0].content


@pytest.mark.asyncio
async def test_falsifier_17_telegram_typing_indicator_weather_lookup_and_confirmation(db_session):
    """Falsifier 17.6: Telegram displays typing indicator, executes weather lookup, and resolves confirmations contextually."""
    app.dependency_overrides[get_db] = lambda: db_session

    redis = await get_redis()
    await redis.set("channel:telegram:token", "mock-token-123")
    await redis.set("channel:telegram:chat_id", "987654321")
    await redis.set("channel:telegram:enabled", "true")
    await redis.set("operator:location", "KS")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Test weather query & typing indicator
        mock_client = create_mock_httpx_client()
        with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
            chat_payload = {
                "update_id": 4001,
                "message": {
                    "message_id": 101,
                    "from": {"id": 111, "username": "bigknoxy777"},
                    "chat": {"id": 987654321, "type": "private"},
                    "text": "What's the weather in Topeka ks right now?",
                },
            }
            res = await client.post("/api/v1/channels/telegram/webhook", json=chat_payload)
            assert res.status_code == 200

            # Verify sendChatAction: typing was sent
            action_calls = [
                c[1]["json"]
                for c in mock_client.post.call_args_list
                if "sendChatAction" in c[0][0]
            ]
            assert len(action_calls) >= 1
            assert action_calls[0].get("action") == "typing"

            # Verify weather reply was sent
            sent_msgs = [
                c[1]["json"]
                for c in mock_client.post.call_args_list
                if "sendMessage" in c[0][0]
            ]
            assert len(sent_msgs) >= 1
            reply_text = sent_msgs[-1].get("text", "")
            assert "weather" in reply_text.lower() or "temperature" in reply_text.lower() or "°" in reply_text

        # 2. Test contextual confirmation "Yes do it"
        # Seed conversation history in redis for this chat
        history_key = "channel:telegram:history:987654321"
        seed_history = [
            {"role": "user", "content": "Can you check the weather?"},
            {"role": "assistant", "content": "I can check the weather in Topeka, KS for you. Would you like me to do that?"},
        ]
        await redis.set(history_key, json.dumps(seed_history), ex=3600)

        mock_client_2 = create_mock_httpx_client()
        with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client_2):
            confirm_payload = {
                "update_id": 4002,
                "message": {
                    "message_id": 102,
                    "from": {"id": 111, "username": "bigknoxy777"},
                    "chat": {"id": 987654321, "type": "private"},
                    "text": "Yes do it",
                },
            }
            res_confirm = await client.post("/api/v1/channels/telegram/webhook", json=confirm_payload)
            assert res_confirm.status_code == 200

            # Verify confirmation triggered weather lookup
            confirm_msgs = [
                c[1]["json"]
                for c in mock_client_2.post.call_args_list
                if "sendMessage" in c[0][0]
            ]
            assert len(confirm_msgs) >= 1
            confirm_reply = confirm_msgs[-1].get("text", "")
            assert "weather" in confirm_reply.lower() or "°" in confirm_reply or "topeka" in confirm_reply.lower()


@pytest.mark.asyncio
async def test_falsifier_17_concurrent_telegram_approvals_and_markup_replacement():
    """Falsifier 17.7: Multiple concurrent Telegram HITL callbacks must both succeed and update inline buttons."""
    import asyncio
    from app.database import AsyncSessionLocal
    app.dependency_overrides.pop(get_db, None)

    redis = await get_redis()
    await redis.set("channel:telegram:token", "mock-token-123")
    await redis.set("channel:telegram:chat_id", "987654321")

    # Create two pending tickets using an isolated session
    ticket_1_id = uuid.uuid4()
    ticket_2_id = uuid.uuid4()
    async with AsyncSessionLocal() as session:
        t1 = HITLTicketModel(
            id=ticket_1_id,
            workflow_id="code_review_pipeline",
            task_id="task-multi-1",
            agent_id="coder",
            gate_type="tool_approval",
            tool_id="git_branch_commit",
            tool_arguments={"branch": "feat/tg-1", "message": "First concurrent approval"},
            status="PENDING",
        )
        t2 = HITLTicketModel(
            id=ticket_2_id,
            workflow_id="code_review_pipeline",
            task_id="task-multi-2",
            agent_id="coder",
            gate_type="tool_approval",
            tool_id="git_branch_commit",
            tool_arguments={"branch": "feat/tg-2", "message": "Second concurrent approval"},
            status="PENDING",
        )
        session.add_all([t1, t2])
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        mock_client = create_mock_httpx_client()
        with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
            cb1 = {
                "update_id": 5001,
                "callback_query": {
                    "id": "cb_query_1",
                    "from": {"id": 111, "username": "bigknoxy777"},
                    "message": {
                        "message_id": 201,
                        "chat": {"id": 987654321},
                        "text": "🛡️ [Blackboard Tool HITL] Agent coder requested git_branch_commit 1",
                    },
                    "data": f"hitl:{ticket_1_id}:APPROVED:tool",
                },
            }
            cb2 = {
                "update_id": 5002,
                "callback_query": {
                    "id": "cb_query_2",
                    "from": {"id": 111, "username": "bigknoxy777"},
                    "message": {
                        "message_id": 202,
                        "chat": {"id": 987654321},
                        "text": "🛡️ [Blackboard Tool HITL] Agent coder requested git_branch_commit 2",
                    },
                    "data": f"hitl:{ticket_2_id}:APPROVED:tool",
                },
            }

            # Fire both approvals concurrently
            res1, res2 = await asyncio.gather(
                client.post("/api/v1/channels/telegram/webhook", json=cb1),
                client.post("/api/v1/channels/telegram/webhook", json=cb2),
            )
            assert res1.status_code == 200
            assert res2.status_code == 200

            # Verify both callbacks were answered
            answer_calls = [
                c[1]["json"]
                for c in mock_client.post.call_args_list
                if "answerCallbackQuery" in c[0][0]
            ]
            assert len(answer_calls) >= 2

            # Verify editMessageText was called for BOTH message IDs (201 and 202)
            edit_calls = [
                c[1]["json"]
                for c in mock_client.post.call_args_list
                if "editMessageText" in c[0][0]
            ]
            edit_msg_ids = {c.get("message_id") for c in edit_calls}
            assert 201 in edit_msg_ids
            assert 202 in edit_msg_ids

            # Verify reply_markup in editMessageText includes resolved status badge
            for c in edit_calls:
                markup = c.get("reply_markup", {})
                kb = markup.get("inline_keyboard", [])
                assert len(kb) > 0
                first_btn_text = kb[0][0].get("text", "")
                assert "APPROVED" in first_btn_text or "✅" in first_btn_text

        # Verify both tickets are APPROVED in DB using isolated session
        async with AsyncSessionLocal() as session:
            db_t1 = await session.get(HITLTicketModel, ticket_1_id)
            db_t2 = await session.get(HITLTicketModel, ticket_2_id)
            assert db_t1.status == "APPROVED"
            assert db_t2.status == "APPROVED"
            assert db_t1.reviewer_id == "@bigknoxy777"
            assert db_t2.reviewer_id == "@bigknoxy777"


@pytest.mark.asyncio
async def test_falsifier_17_live_sports_scoreboard_accuracy(db_session):
    """Falsifier 17.8: Live sports queries via web_search retrieve real scoreboard data without hallucinating opponents."""
    from app.mcp_gateway import mcp_gateway, ToolInvokeRequest
    app.dependency_overrides[get_db] = lambda: db_session

    req = ToolInvokeRequest(
        agent_id="researcher",
        workflow_id="test_wf",
        task_id="test_task",
        tool_id="web_search",
        arguments={"query": "What was the score of the KC Chiefs game today?"},
        bypass_approval=True,
    )
    result = await mcp_gateway.invoke_tool(db_session, req)
    assert result.status == "EXECUTED"
    res_data = result.result
    assert "results" in res_data
    items = res_data["results"]
    assert len(items) > 0
    joined_snippets = " ".join([i.get("snippet", "") for i in items])
    # Must contain Chiefs and genuine NFL team
    assert "Chiefs" in joined_snippets
    assert "NFL" in joined_snippets or "Kansas City" in joined_snippets


@pytest.mark.asyncio
async def test_falsifier_17_telegram_reply_context_research_memory_and_html_formatting(db_session):
    """Falsifier 17.9: Telegram reply_to_message preserves context, executes list_memories, and sends HTML-formatted replies."""
    from app.models import AgentModel, AgentMemoryModel
    from app.concierge import compute_embedding

    app.dependency_overrides[get_db] = lambda: db_session
    redis = await get_redis()
    await redis.set("channel:telegram:token", "mock-token-123")
    await redis.set("channel:telegram:chat_id", "987654321")

    res_agent = await db_session.get(AgentModel, "researcher")
    if not res_agent:
        res_agent = AgentModel(
            id="researcher",
            name="Researcher",
            role="Deep Research Agent",
            system_prompt="Research systems.",
            allowed_tools=["web_search"],
        )
        db_session.add(res_agent)
        await db_session.commit()

    emb = await compute_embedding("Archon architecture patterns")
    mem = AgentMemoryModel(
        agent_id="researcher",
        memory_type="semantic",
        content="Archon uses a multi-agent advisor + refiner architecture with modular MCP tools.",
        embedding=emb,
    )
    db_session.add(mem)
    await db_session.commit()

    payload = {
        "update_id": 3001,
        "message": {
            "message_id": 501,
            "from": {"id": 111, "username": "bigknoxy777"},
            "chat": {"id": 987654321},
            "text": "Did you conduct the research?",
            "reply_to_message": {
                "message_id": 499,
                "text": "I'll conduct thorough research on Archon's architecture and create a detailed enhancement plan.",
            },
        },
    }

    mock_client = create_mock_httpx_client()
    try:
        with patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client), \
             patch("app.concierge.ConciergeAdminAgent._try_ollama_chat_with_tools", return_value=(None, [])):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                res = await client.post("/api/v1/channels/telegram/webhook", json=payload)
                assert res.status_code == 200
                reply = res.json().get("reply", "")
                assert "researcher" in reply
                assert "Archon uses a multi-agent advisor" in reply

            tg_send_calls = [
                c[1]["json"]
                for c in mock_client.post.call_args_list
                if "sendMessage" in c[0][0]
            ]
            assert len(tg_send_calls) >= 1
            sent_payload = tg_send_calls[-1]
            all_sent_text = "\n".join(c.get("text", "") for c in tg_send_calls)
            assert sent_payload.get("parse_mode") == "HTML"
            assert "<b>" in all_sent_text
            assert "**" not in all_sent_text
    finally:
        await db_session.delete(mem)
        await db_session.commit()


@pytest.mark.asyncio
async def test_falsifier_17_concierge_native_tool_calls_react_loop(db_session):
    """Falsifier 17.10: Concierge ReAct loop executes native OpenAI tool_calls and feeds observations to turn 2."""
    from app.concierge import concierge_agent
    from app.schemas import ConciergeChatRequest

    turn_calls = []

    async def fake_chat_with_tools(user_msg, snapshot, executed_tools, history=None):
        turn_calls.append(len(executed_tools))
        if len(turn_calls) == 1:
            return (
                None,
                [{"tool": "list_backlog", "arguments": {}, "extracted_from_llm": True}],
            )
        return ("Checked the backlog: all items retrieved.", [])

    with patch.object(
        concierge_agent, "_try_ollama_chat_with_tools", side_effect=fake_chat_with_tools
    ):
        resp = await concierge_agent.handle_chat(
            db_session,
            ConciergeChatRequest(message="Check status of our enhancement plan"),
        )
        assert len(turn_calls) == 2
        assert any(t.get("tool") == "list_backlog" for t in resp.tool_calls_executed)
        assert "Checked the backlog" in resp.reply


@pytest.mark.asyncio
async def test_falsifier_17_generic_research_plan_verify_workflow_dispatch(db_session):
    """Falsifier 17.11: Generic research_plan_verify_pipeline has REWORK verification loop and is dispatched by Concierge."""
    from pathlib import Path
    from app.models import WorkflowModel
    from app.workflow_engine import workflow_engine
    from app.concierge import concierge_agent
    from app.schemas import ConciergeChatRequest

    yaml_path = Path("/workflows/research_plan_verify_pipeline.yaml")
    if not yaml_path.exists():
        yaml_path = Path(__file__).resolve().parents[2] / "workflows" / "research_plan_verify_pipeline.yaml"
    assert yaml_path.exists(), f"Missing {yaml_path}"
    yaml_src = yaml_path.read_text(encoding="utf-8")
    graph = workflow_engine.parse_yaml_to_graph(yaml_src)
    rework_edges = [e for e in graph["edges"] if e.get("condition") == "REWORK"]
    pass_edges = [e for e in graph["edges"] if e.get("condition") == "PASS"]
    assert len(rework_edges) == 1 and rework_edges[0]["target"] == "stage_deep_research"
    assert len(pass_edges) == 1 and pass_edges[0]["target"] == "end_research_plan"

    wf = await db_session.get(WorkflowModel, "research_plan_verify_pipeline")
    if not wf:
        wf = WorkflowModel(
            id="research_plan_verify_pipeline",
            name="Generic Research, Planning & Iterative Verification Pipeline",
            yaml_source=yaml_src,
            bpmn_xml=workflow_engine.yaml_to_bpmn(yaml_src),
        )
        db_session.add(wf)
        await db_session.commit()

    with patch.object(
        concierge_agent, "_try_ollama_chat_with_tools", return_value=(None, [])
    ):
        resp = await concierge_agent.handle_chat(
            db_session,
            ConciergeChatRequest(
                message="Yes, do planning and research on Archon patterns and add to the backlog"
            ),
        )
        trig = [t for t in resp.tool_calls_executed if t.get("tool") == "trigger_workflow"]
        assert len(trig) >= 1
        assert trig[0]["workflow_id"] == "research_plan_verify_pipeline"
        assert trig[0].get("run_id")

        # Explicit "Run <workflow_id>" command also triggers deterministically
        wf_cr = await db_session.get(WorkflowModel, "code_review_pipeline")
        if not wf_cr:
            wf_cr = WorkflowModel(
                id="code_review_pipeline",
                name="Code Review Pipeline",
                state_graph={"nodes": [{"id": "s", "type": "StartEvent"}, {"id": "e", "type": "EndEvent"}], "edges": [{"id": "f1", "source": "s", "target": "e"}]},
                yaml_source="id: code_review_pipeline\nname: Code Review\nnodes: []\nedges: []\n",
                bpmn_xml="<definitions/>",
            )
            db_session.add(wf_cr)
            await db_session.commit()
        resp_cr = await concierge_agent.handle_chat(
            db_session,
            ConciergeChatRequest(message="Run code_review_pipeline to review and test the channel gateway changes"),
        )
        trig_cr = [t for t in resp_cr.tool_calls_executed if t.get("tool") == "trigger_workflow"]
        assert len(trig_cr) >= 1
        assert trig_cr[0]["workflow_id"] == "code_review_pipeline"
        assert trig_cr[0].get("run_id")


@pytest.mark.asyncio
async def test_falsifier_17_workflow_completed_channel_agnostic_notification(db_session, fake_redis):
    """Falsifier 17.12: EndEvent summarizes Blackboard entries, publishes workflow.completed, and notifies all channels via ChannelGateway."""
    import uuid
    from unittest.mock import AsyncMock
    from sqlalchemy import select
    from app.models import BlackboardEntryModel, WorkflowModel, WorkflowRunModel
    from app.workflow_engine import workflow_engine

    wf_id = f"wf-notify-{uuid.uuid4().hex[:6]}"
    wf = WorkflowModel(
        id=wf_id,
        name="Notify Test Workflow",
        yaml_source=f"""
id: {wf_id}
name: Notify Test Workflow
nodes:
  - id: start
    type: StartEvent
  - id: done
    type: EndEvent
edges:
  - source: start
    target: done
""",
        bpmn_xml="<definitions/>",
    )
    db_session.add(wf)
    await db_session.flush()

    run = WorkflowRunModel(
        workflow_id=wf_id,
        status="RUNNING",
        current_nodes=["done"],
        completed_nodes=["start"],
        context={"initial_prompt": "Research patterns", "origin_channel": "telegram"},
    )
    db_session.add(run)
    await db_session.flush()

    db_session.add(
        BlackboardEntryModel(
            workflow_id=wf_id,
            task_id="task-research-1",
            agent_id="researcher",
            entry_type="finding",
            content="Found 3 relevant architecture patterns.",
            metadata_={},
        )
    )
    await db_session.commit()

    published_events = []

    async def fake_publish(payload):
        published_events.append(payload)

    # Configure Telegram & Discord in Redis and capture outbound HTTP calls
    await fake_redis.set("channel:telegram:token", "123:ABC")
    await fake_redis.set("channel:telegram:chat_id", "99999")
    await fake_redis.set("channel:telegram:enabled", "true")
    await fake_redis.set("channel:discord:webhook", "https://discord.com/api/webhooks/test/hook")
    await fake_redis.set("channel:discord:enabled", "true")

    http_posts = []

    class FakeResp:
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, **kwargs):
            http_posts.append((url, json))
            return FakeResp()

    with patch.object(workflow_engine, "_publish_event", side_effect=fake_publish), \
         patch("app.channel_gateway.get_redis", new=AsyncMock(return_value=fake_redis)), \
         patch("app.channel_gateway.httpx.AsyncClient", FakeAsyncClient):
        await workflow_engine._activate_node(db_session, wf, run, {"id": "done", "type": "EndEvent"})
        await db_session.commit()
        # Let scheduled background notification task run
        import asyncio
        await asyncio.sleep(0.05)

    assert run.status == "COMPLETED"
    assert len(published_events) == 1
    ev = published_events[0]
    assert ev["event"] == "workflow.completed"
    assert ev["workflow_id"] == wf_id
    assert "Found 3 relevant architecture patterns." in ev["summary"]

    # Summary entry persisted to Blackboard
    sum_res = await db_session.execute(
        select(BlackboardEntryModel).where(
            BlackboardEntryModel.workflow_id == wf_id,
            BlackboardEntryModel.entry_type == "summary",
        )
    )
    sum_entry = sum_res.scalars().first()
    assert sum_entry is not None
    assert "Found 3 relevant architecture patterns." in sum_entry.content

    # Both Telegram (HTML) and Discord (embed) received the completion notification
    tg_posts = [p for p in http_posts if "api.telegram.org" in p[0]]
    dc_posts = [p for p in http_posts if "discord.com" in p[0]]
    assert len(tg_posts) == 1
    assert tg_posts[0][1]["parse_mode"] == "HTML"
    assert "Found 3 relevant architecture patterns." in tg_posts[0][1]["text"]
    assert len(dc_posts) == 1
    assert dc_posts[0][1]["embeds"][0]["title"].startswith("✅ Workflow COMPLETED")


@pytest.mark.asyncio
async def test_falsifier_17_meta_workflow_upsert_enforces_lifecycle_events(db_session):
    """Falsifier 17.13: Meta-workflow creation/updates auto-wire StartEvent/EndEvent and emit workflow.created/updated Blackboard events."""
    import uuid
    from app.workflow_engine import workflow_engine

    wf_id = f"wf-meta-{uuid.uuid4().hex[:6]}"
    # Deliberately omit StartEvent and EndEvent from the meta-generated YAML
    incomplete_yaml = f"""
id: {wf_id}
name: Meta Generated Workflow
nodes:
  - id: step_research
    type: ServiceTask
    agent: researcher
    name: Research
edges: []
"""
    published = []

    async def fake_publish(payload):
        published.append(payload)

    with patch.object(workflow_engine, "_publish_event", side_effect=fake_publish):
        wf = await workflow_engine.upsert_workflow_from_yaml(
            session=db_session,
            workflow_id=wf_id,
            name="Meta Generated Workflow",
            description="Created without explicit Start/End events",
            yaml_content=incomplete_yaml,
            source="workflow_activator",
        )
        node_types = {n["type"] for n in wf.state_graph["nodes"]}
        assert "StartEvent" in node_types
        assert "EndEvent" in node_types
        edges = wf.state_graph["edges"]
        assert any(e["target"] == "step_research" for e in edges)
        assert any(e["source"] == "step_research" and e["target"] == "end_event" for e in edges)
        assert published[-1]["event"] == "workflow.created"
        assert published[-1]["workflow_id"] == wf_id

        # Update the same workflow and verify workflow.updated is emitted
        wf2 = await workflow_engine.upsert_workflow_from_yaml(
            session=db_session,
            workflow_id=wf_id,
            name="Meta Generated Workflow v2",
            description="Updated",
            yaml_content=incomplete_yaml,
            source="workflow_activator",
        )
        assert wf2.name == "Meta Generated Workflow v2"
        assert published[-1]["event"] == "workflow.updated"
        assert published[-1]["workflow_id"] == wf_id


@pytest.mark.asyncio
async def test_falsifier_17_workflow_api_and_channel_broadcast_solid(db_session):
    """Falsifier 17.14: REST create/update workflow auto-wires lifecycle events & ChannelGateway.register_channel isolates failures."""
    import uuid
    from app.workflow_engine import workflow_engine
    from app.channel_gateway import BaseChannel, ChannelGateway
    from app.main import create_workflow, update_workflow
    from app.schemas import WorkflowCreate, WorkflowUpdate, WorkflowStateGraph

    # 1. ensure_lifecycle_events handles empty graph and dangling node with existing EndEvent
    assert workflow_engine.ensure_lifecycle_events({"nodes": [], "edges": []}) == {"nodes": [], "edges": []}
    dangling = workflow_engine.ensure_lifecycle_events(
        {
            "nodes": [
                {"id": "s", "type": "StartEvent"},
                {"id": "t1", "type": "ServiceTask"},
                {"id": "e", "type": "EndEvent"},
            ],
            "edges": [{"id": "f1", "source": "s", "target": "t1"}],
        }
    )
    assert any(edge["source"] == "t1" and edge["target"] == "e" for edge in dangling["edges"])

    # 2. REST create_workflow & update_workflow emit workflow.created / workflow.updated and wire EndEvent
    wf_id = f"wf-api-{uuid.uuid4().hex[:6]}"
    published = []

    async def fake_publish(payload):
        published.append(payload)

    with patch.object(workflow_engine, "_publish_event", side_effect=fake_publish):
        created = await create_workflow(
            WorkflowCreate(
                id=wf_id,
                name="API Test Workflow",
                state_graph=WorkflowStateGraph(
                    nodes=[{"id": "step_1", "type": "ServiceTask", "name": "Step 1"}],
                    edges=[],
                ),
            ),
            session=db_session,
        )
        assert any(n["type"] == "EndEvent" for n in created.state_graph["nodes"])
        assert published[-1]["event"] == "workflow.created"

        updated = await update_workflow(
            wf_id,
            WorkflowUpdate(name="API Test Workflow v2"),
            session=db_session,
        )
        assert updated.name == "API Test Workflow v2"
        assert published[-1]["event"] == "workflow.updated"

    # 3. ChannelGateway Open/Closed register_channel & per-channel error isolation
    gw = ChannelGateway()

    class FailingChannel(BaseChannel):
        async def send_text(self, text):
            raise RuntimeError("boom")

        async def send_hitl_notification(self, ticket):
            raise RuntimeError("boom")

        async def send_workflow_completed(self, workflow_id, run_id, summary, status="COMPLETED", metadata=None):
            raise RuntimeError("boom")

    class RecordingChannel(BaseChannel):
        def __init__(self):
            self.calls = []

        async def send_text(self, text):
            self.calls.append(("text", text))
            return {"status": "DELIVERED"}

        async def send_hitl_notification(self, ticket):
            self.calls.append(("hitl", ticket.id))
            return {"status": "DELIVERED"}

        async def send_workflow_completed(self, workflow_id, run_id, summary, status="COMPLETED", metadata=None):
            self.calls.append(("completed", workflow_id, summary))
            return {"status": "DELIVERED"}

    rec = RecordingChannel()
    gw._channels = {}
    gw.register_channel("failing", FailingChannel())
    gw.register_channel("recording", rec)

    res = await gw.notify_workflow_completed(wf_id, "run-1234", "All done")
    assert res["failing"]["status"] == "ERROR"
    assert res["recording"]["status"] == "DELIVERED"
    assert rec.calls == [("completed", wf_id, "All done")]


@pytest.mark.asyncio
async def test_falsifier_17_deterministic_verification_loop_and_max_retries(db_session):
    """Falsifier 17.15: Deterministic VerificationTask loops back on REWORK, advances on PASS, and halts at max_iterations."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock
    from sqlalchemy import select
    from app.models import BlackboardEntryModel, WorkflowModel, WorkflowRunModel
    from app.schemas import ToolInvokeResponse
    from app.workflow_engine import workflow_engine
    from app.mcp_gateway import mcp_gateway
    from app.channel_gateway import channel_gateway

    wf_id = f"wf-verify-{uuid.uuid4().hex[:6]}"
    yaml_src = f"""
id: {wf_id}
name: Deterministic Verify Loop
nodes:
  - id: start
    type: StartEvent
  - id: fix_code
    type: ServiceTask
    agent_id: coder
    name: Fix Code
  - id: run_tests
    type: VerificationTask
    agent_id: qa-tester
    name: Run Pytest
    verify_command: pytest tests/unit/
    max_iterations: 2
    trusted_command: true
  - id: gate
    type: ExclusiveGateway
    name: Tests Passed?
  - id: done
    type: EndEvent
edges:
  - id: e1
    source: start
    target: fix_code
  - id: e2
    source: fix_code
    target: run_tests
  - id: e3
    source: run_tests
    target: gate
  - id: e4
    source: gate
    target: fix_code
    condition: REWORK
  - id: e5
    source: gate
    target: done
    condition: PASS
"""
    graph = workflow_engine.parse_yaml_to_graph(yaml_src)
    wf = WorkflowModel(
        id=wf_id,
        name="Deterministic Verify Loop",
        state_graph={"nodes": graph["nodes"], "edges": graph["edges"]},
        yaml_source=yaml_src,
        bpmn_xml="<definitions/>",
    )
    db_session.add(wf)
    await db_session.flush()

    run = WorkflowRunModel(
        workflow_id=wf_id,
        status="RUNNING",
        current_nodes=["run_tests"],
        completed_nodes=["start", "fix_code"],
        context={"initial_prompt": "Fix failing test"},
    )
    db_session.add(run)
    await db_session.commit()

    # 1. Fail once -> REWORK loops back to fix_code, then pass on 2nd check -> COMPLETED
    outcomes = iter([
        ToolInvokeResponse(status="EXECUTED", result={"exit_code": 1, "stderr": "AssertionError: 1 != 2"}),
        ToolInvokeResponse(status="EXECUTED", result={"exit_code": 0, "stdout": "5 passed"}),
    ])
    published = []
    fake_redis = MagicMock()
    fake_redis.xadd = AsyncMock(return_value="1-0")

    async def fake_invoke(_session, _req):
        return next(outcomes)

    async def fake_pub(ev):
        published.append(ev)

    verify_node = next(n for n in wf.state_graph["nodes"] if n["id"] == "run_tests")

    with patch.object(mcp_gateway, "invoke_tool", side_effect=fake_invoke), \
         patch.object(workflow_engine, "_publish_event", side_effect=fake_pub), \
         patch("app.workflow_engine.get_redis", return_value=fake_redis), \
         patch.object(channel_gateway, "notify_workflow_completed", new=AsyncMock(return_value={})):
        await workflow_engine._activate_node(db_session, wf, run, verify_node)
        assert run.context["last_verdict"] == "REWORK"
        assert run.context["retry_counts"]["run_tests"] == 1
        assert "fix_code" in run.current_nodes

        # Simulate coder finishing fix and re-entering run_tests -> passes
        await workflow_engine._activate_node(db_session, wf, run, verify_node)
        assert run.status == "COMPLETED"
        assert run.context["last_verdict"] == "PASS"

    critiques = await db_session.execute(
        select(BlackboardEntryModel).where(
            BlackboardEntryModel.workflow_id == wf_id,
            BlackboardEntryModel.entry_type == "critique",
        )
    )
    assert "AssertionError: 1 != 2" in critiques.scalars().first().content

    # 2. Exceeding max_iterations=2 halts run with FAILED and notifies ChannelGateway
    run2 = WorkflowRunModel(
        workflow_id=wf_id,
        status="RUNNING",
        current_nodes=["run_tests"],
        completed_nodes=["start", "fix_code"],
        context={"initial_prompt": "Persistent failure", "retry_counts": {"run_tests": 1}},
    )
    db_session.add(run2)
    await db_session.commit()

    notify_mock = AsyncMock(return_value={})
    with patch.object(
        mcp_gateway,
        "invoke_tool",
        new=AsyncMock(return_value=ToolInvokeResponse(status="EXECUTED", result={"exit_code": 2, "stderr": "boom"})),
    ), \
         patch.object(workflow_engine, "_publish_event", side_effect=fake_pub), \
         patch.object(channel_gateway, "notify_workflow_completed", new=notify_mock):
        await workflow_engine._activate_node(db_session, wf, run2, verify_node)
        import asyncio
        await asyncio.sleep(0.05)

    assert run2.status == "FAILED"
    assert run2.context["last_verdict"] == "FAIL_LIMIT"
    assert any(e.get("event") == "workflow.failed" for e in published)
    notify_mock.assert_awaited_once()
    assert notify_mock.call_args.kwargs["status"] == "FAILED"


@pytest.mark.asyncio
async def test_falsifier_17_intent_classifier_routing_and_confidence_thresholds(db_session):
    """Falsifier 17.16: Lightweight structured classifier routes natural-language workflow intents, clarifies low-confidence ambiguity, and degrades safely."""
    from unittest.mock import AsyncMock
    from app.concierge import concierge_agent
    from app.models import WorkflowModel
    from app.schemas import ConciergeChatRequest

    if not await db_session.get(WorkflowModel, "code_review_pipeline"):
        db_session.add(
            WorkflowModel(
                id="code_review_pipeline",
                name="Code Review Pipeline",
                state_graph={"nodes": [{"id": "s", "type": "StartEvent"}, {"id": "e", "type": "EndEvent"}], "edges": [{"id": "f1", "source": "s", "target": "e"}]},
                yaml_source="id: code_review_pipeline\nname: Code Review\nnodes: []\nedges: []\n",
                bpmn_xml="<definitions/>",
            )
        )
        await db_session.commit()

    # 1. High-confidence trigger_workflow dispatches even if main LLM returns plain prose
    with patch.object(
        concierge_agent,
        "_classify_intent",
        new=AsyncMock(return_value={"intent": "trigger_workflow", "workflow_id": "code_review_pipeline", "confidence": 0.92}),
    ), patch.object(
        concierge_agent,
        "_try_ollama_chat_with_tools",
        new=AsyncMock(return_value=("Started the review.", [])),
    ):
        resp_hi = await concierge_agent.handle_chat(
            db_session,
            ConciergeChatRequest(message="Can you have the team audit the auth module and run tests?"),
        )
        trig = [t for t in resp_hi.tool_calls_executed if t.get("tool") == "trigger_workflow"]
        assert len(trig) == 1
        assert trig[0]["workflow_id"] == "code_review_pipeline"
        assert trig[0]["routed_by"] == "intent_classifier"
        assert trig[0]["run_id"]

    # 2. Mid-confidence trigger_workflow asks a 1-line clarifying question instead of guessing
    with patch.object(
        concierge_agent,
        "_classify_intent",
        new=AsyncMock(return_value={"intent": "trigger_workflow", "workflow_id": "code_review_pipeline", "confidence": 0.58}),
    ):
        resp_mid = await concierge_agent.handle_chat(
            db_session,
            ConciergeChatRequest(message="Maybe look into the auth module later"),
        )
        clar = [t for t in resp_mid.tool_calls_executed if t.get("tool") == "intent_classifier"]
        assert len(clar) == 1
        assert clar[0]["status"] == "NEEDS_CLARIFICATION"
        assert "Did you want me to start a workflow" in resp_mid.reply

    # 3. High-confidence inspect_state pre-executes list_blackboard
    with patch.object(
        concierge_agent,
        "_classify_intent",
        new=AsyncMock(return_value={"intent": "inspect_state", "workflow_id": None, "confidence": 0.88}),
    ), patch.object(
        concierge_agent,
        "_try_ollama_chat_with_tools",
        new=AsyncMock(return_value=(None, [])),
    ):
        resp_insp = await concierge_agent.handle_chat(
            db_session,
            ConciergeChatRequest(message="What are the agents currently posting on the board?"),
        )
        bb = [t for t in resp_insp.tool_calls_executed if t.get("tool") == "list_blackboard"]
        assert len(bb) == 1
        assert bb[0]["routed_by"] == "intent_classifier"

    # 4. Classifier handles malformed JSON / offline endpoint safely -> chat fallback
    class BadHttpClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise ConnectionError("offline")

    with patch("app.concierge.httpx.AsyncClient", BadHttpClient):
        fb = await concierge_agent._classify_intent("Hello there", ["code_review_pipeline"])
        assert fb["intent"] == "chat"
        assert fb["confidence"] == 0.0


@pytest.mark.asyncio
async def test_falsifier_17_telegram_update_error_fallback_and_polling_warnings(caplog):
    """Fix 2: _process_single_telegram_update sends fallback error reply; polling logs non-200 & exceptions at warning."""
    import logging
    from contextlib import asynccontextmanager
    from app.channel_gateway import ChannelGateway

    gw = ChannelGateway()
    mock_client = create_mock_httpx_client()

    @asynccontextmanager
    async def dummy_session_cm():
        yield MagicMock()

    with caplog.at_level(logging.WARNING, logger="blackboard.channels"), \
         patch("app.channel_gateway.AsyncSessionLocal", side_effect=dummy_session_cm), \
         patch.object(
             gw.telegram,
             "get_credentials",
             new=AsyncMock(return_value={"token": "123:TEST", "chat_id": "777", "enabled": True, "cockpit_url": "http://localhost:5173"}),
         ), \
         patch.object(
             gw,
             "process_telegram_webhook",
             new=AsyncMock(side_effect=RuntimeError("boom float32")),
         ), \
         patch("app.channel_gateway.httpx.AsyncClient", return_value=mock_client):
        await gw._process_single_telegram_update(
            {"update_id": 9001, "message": {"chat": {"id": 777}, "text": "hello"}}
        )
        await gw._process_single_telegram_update(
            {"update_id": 9002, "callback_query": {"id": "cb1", "message": {"chat": {"id": 888}}}}
        )

    assert any("Error processing telegram update 9001" in rec.message for rec in caplog.records)
    send_calls = [
        c[1]["json"]
        for c in mock_client.post.call_args_list
        if "sendMessage" in c[0][0]
    ]
    assert len(send_calls) == 2
    assert send_calls[0]["chat_id"] == 777
    assert "boom float32" in send_calls[0]["text"]
    assert send_calls[1]["chat_id"] == 888

    # Polling logs 409 Conflict at WARNING and does NOT send drop_pending_updates
    caplog.clear()
    seen_params = []

    class ConflictPollingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None, **kwargs):
            seen_params.append(dict(params or {}))
            gw._polling_running = False
            return MagicMock(status_code=409, text='{"ok":false,"error_code":409}')

    with caplog.at_level(logging.WARNING, logger="blackboard.channels"), \
         patch.object(
             gw.telegram,
             "get_credentials",
             new=AsyncMock(return_value={"token": "123:LIVE", "chat_id": "777", "enabled": True, "cockpit_url": "http://localhost:5173"}),
         ), \
         patch("app.channel_gateway.httpx.AsyncClient", ConflictPollingClient), \
         patch("app.channel_gateway.asyncio.sleep", new=AsyncMock()):
        await gw.start_telegram_polling()

    assert len(seen_params) == 1
    assert "drop_pending_updates" not in seen_params[0]
    assert any(
        rec.levelno == logging.WARNING and "409" in rec.message
        for rec in caplog.records
    )





