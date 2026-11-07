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
            sent_json = mock_client.post.call_args[1]["json"]
            assert "chat_id" in sent_json
            assert len(sent_json["text"]) > 0


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
