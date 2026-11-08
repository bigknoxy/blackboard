"""
channel_gateway.py — External Chat Channels Gateway (Telegram & Discord)

Implements SOLID Principles:
- Single Responsibility (SRP): Centralizes external channel communications,
  formatting, inline interactive buttons, and webhook callback processing.
- Open/Closed (OCP): Extensible channel abstractions (BaseChannel, TelegramChannel, DiscordChannel).
- Dependency Inversion (DIP): Workflow engine and MCP gateway depend on channel_gateway,
  not direct HTTP webhook calls or ad-hoc formatting.
"""

import asyncio
import logging
import os
import json
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_redis, AsyncSessionLocal
from app.models import (
    HITLTicketModel,
    ApprovalPolicyModel,
    BlackboardEntryModel,
    AgentModel,
    WorkflowModel,
    WorkflowRunModel,
)
from app.schemas import (
    ChannelConfigRequest,
    ChannelIncomingMessage,
    ConciergeChatRequest,
    ToolInvokeRequest,
)

logger = logging.getLogger("blackboard.channel_gateway")


class BaseChannel(ABC):
    """Abstract interface for external notification & chat channels."""

    @abstractmethod
    async def send_text(self, text: str) -> Dict[str, Any]:
        """Send a standard text notification."""
        pass

    @abstractmethod
    async def send_hitl_notification(self, ticket: HITLTicketModel) -> Dict[str, Any]:
        """Send an interactive HITL approval card with inline decision buttons."""
        pass


class TelegramChannel(BaseChannel):
    """
    Interactive Telegram Bot Gateway:
    - Inbound commands (/status, /cockpit, /approvals, /steer) and natural chat
    - Outbound notifications with InlineKeyboardMarkup for 1-tap mobile decisions
    - Callback query processing (resolves tickets and graduates trust directly from Telegram)
    - Telegram Mini-App (TWA) launcher
    """

    def __init__(self):
        self.default_cockpit_url = os.getenv("COCKPIT_URL", "http://192.168.1.100:5173")

    async def get_credentials(self) -> Dict[str, Any]:
        redis_client = await get_redis()
        token = await redis_client.get("channel:telegram:token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = await redis_client.get("channel:telegram:chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")
        en_raw = await redis_client.get("channel:telegram:enabled")
        enabled = (str(en_raw).lower() in ("true", "1", "none")) if en_raw is not None else bool(token and chat_id)
        cockpit_url = await redis_client.get("channel:cockpit:url") or self.default_cockpit_url
        return {
            "token": str(token).strip() if token else "",
            "chat_id": str(chat_id).strip() if chat_id else "",
            "enabled": enabled,
            "cockpit_url": str(cockpit_url).strip(),
        }

    async def send_text(self, text: str) -> Dict[str, Any]:
        creds = await self.get_credentials()
        if not creds["token"] or not creds["chat_id"] or not creds["enabled"]:
            return {"status": "SKIPPED", "reason": "not configured or disabled"}

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.post(
                    f"https://api.telegram.org/bot{creds['token']}/sendMessage",
                    json={
                        "chat_id": creds["chat_id"],
                        "text": text[:4000],
                        "parse_mode": "Markdown",
                    },
                )
                return {"status": "DELIVERED", "http_code": res.status_code}
        except Exception as exc:
            logger.warning("Telegram send_text failed: %s", exc)
            return {"status": "ERROR", "error": str(exc)}

    async def send_hitl_notification(self, ticket: HITLTicketModel) -> Dict[str, Any]:
        creds = await self.get_credentials()
        if not creds["token"] or not creds["chat_id"] or not creds["enabled"]:
            return {"status": "SKIPPED", "reason": "not configured or disabled"}

        # Command or args preview
        cmd_str = ""
        if ticket.tool_arguments and "command" in ticket.tool_arguments:
            cmd_str = f"\n`$ {ticket.tool_arguments['command']}`"

        card_text = (
            f"🔒 *[Blackboard HITL Gate]* Approval Required!\n\n"
            f"• *Tool:* `{ticket.tool_id}`\n"
            f"• *Agent:* `@{ticket.agent_id}`\n"
            f"• *Pipeline:* `{ticket.workflow_id}`\n"
            f"• *Gate Type:* `{ticket.gate_type}`\n"
            f"• *Ticket ID:* `{ticket.id}`"
            f"{cmd_str}\n\n"
            f"_Choose an action below to resolve immediately from Telegram:_"
        )

        cockpit_url = creds["cockpit_url"]
        is_https = cockpit_url.startswith("https://")

        # Telegram inline keyboard buttons: Approve Once, Always Allow, Reject, WebApp
        keyboard = [
            [
                {
                    "text": "✅ Approve Once",
                    "callback_data": f"hitl:{ticket.id}:APPROVED:once",
                },
                {
                    "text": "🛡️ Always Allow",
                    "callback_data": f"hitl:{ticket.id}:APPROVED:tool",
                },
                {
                    "text": "❌ Reject",
                    "callback_data": f"hitl:{ticket.id}:REJECTED:once",
                },
            ]
        ]

        if is_https:
            keyboard.append([{"text": "🌐 Open Web Cockpit", "web_app": {"url": cockpit_url}}])
        else:
            keyboard.append([{"text": "🌐 Open Web Cockpit", "url": cockpit_url}])

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.post(
                    f"https://api.telegram.org/bot{creds['token']}/sendMessage",
                    json={
                        "chat_id": creds["chat_id"],
                        "text": card_text,
                        "parse_mode": "Markdown",
                        "reply_markup": {"inline_keyboard": keyboard},
                    },
                )
                return {"status": "DELIVERED", "http_code": res.status_code}
        except Exception as exc:
            logger.warning("Telegram send_hitl_notification failed: %s", exc)
            return {"status": "ERROR", "error": str(exc)}

    async def handle_callback_query(
        self, callback_query: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Process Telegram 1-tap inline button decision.
        Data format: "hitl:{ticket_id}:{decision}:{policy_mode}"
        """
        from app.mcp_gateway import mcp_gateway
        from app.workflow_engine import workflow_engine

        cb_id = callback_query.get("id")
        from_user = callback_query.get("from", {}).get("username") or str(
            callback_query.get("from", {}).get("id", "telegram_operator")
        )
        data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        message_id = message.get("message_id")

        parts = data.split(":")
        if len(parts) < 4 or parts[0] != "hitl":
            return {"status": "IGNORED", "reason": "invalid_callback_data"}

        _, ticket_id_str, decision, policy_mode = parts[:4]
        try:
            ticket_uuid = uuid.UUID(ticket_id_str)
        except ValueError:
            return {"status": "ERROR", "reason": "invalid_uuid"}

        ticket = await session.get(HITLTicketModel, ticket_uuid)
        if not ticket:
            await self._answer_callback(cb_id, "Ticket not found.")
            return {"status": "ERROR", "reason": "ticket_not_found"}

        if ticket.status != "PENDING":
            await self._answer_callback(cb_id, f"Ticket already {ticket.status}.")
            return {"status": "ALREADY_RESOLVED"}

        # Resolve ticket in DB
        reviewer_tag = f"@{from_user}"
        ticket.status = decision
        ticket.reviewer_id = reviewer_tag
        ticket.reviewer_comment = f"Decided via Telegram 1-tap inline button ({decision}:{policy_mode})"
        ticket.resolved_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(ticket)

        tool_result = None
        # Execute tool if approved
        if decision == "APPROVED" and ticket.gate_type == "tool_approval" and ticket.tool_id:
            try:
                invoke_res = await mcp_gateway.invoke_tool(
                    session,
                    ToolInvokeRequest(
                        agent_id=ticket.agent_id,
                        workflow_id=ticket.workflow_id,
                        task_id=ticket.task_id,
                        tool_id=ticket.tool_id,
                        arguments=ticket.tool_arguments or {},
                        bypass_approval=True,
                    ),
                )
                tool_result = invoke_res.result
                ticket.execution_result = tool_result
                await session.commit()
            except Exception as e:
                logger.error("Tool execution following Telegram approval failed: %s", e)

            # Record result on Blackboard
            bb_entry = BlackboardEntryModel(
                workflow_id=ticket.workflow_id,
                task_id=ticket.task_id,
                agent_id=ticket.agent_id,
                entry_type="test_result",
                content=(
                    f"HITL Approved Tool `{ticket.tool_id}` executed.\n"
                    f"Reviewer: {reviewer_tag} (via Telegram)\n"
                    f"Result: {json.dumps(tool_result)}"
                ),
                metadata_={"hitl_ticket_id": str(ticket.id), "channel": "telegram"},
            )
            session.add(bb_entry)
            await session.commit()

        # If policy graduation requested
        if decision == "APPROVED" and policy_mode == "tool" and ticket.tool_id:
            policy = ApprovalPolicyModel(
                policy_type="tool",
                tool_id=ticket.tool_id,
                agent_id=ticket.agent_id,
                workflow_id=ticket.workflow_id,
                enabled=True,
                created_by=reviewer_tag,
            )
            session.add(policy)
            await session.commit()

        # If workflow user_task, advance workflow
        if ticket.gate_type == "user_task":
            run_id_str = (ticket.tool_arguments or {}).get("run_id")
            wf = await session.get(WorkflowModel, ticket.workflow_id)
            if run_id_str and wf:
                try:
                    run = await session.get(WorkflowRunModel, uuid.UUID(str(run_id_str)))
                    if run and decision == "APPROVED":
                        await workflow_engine.advance_from_node(
                            session, wf, run, ticket.tool_id, branch_outcome="PASS"
                        )
                except Exception as ex:
                    logger.warning("Failed to advance workflow from telegram callback: %s", ex)

        # Acknowledge callback query
        ack_text = f"Ticket {decision}! (Policy: {policy_mode})"
        await self._answer_callback(cb_id, ack_text)

        # Update Telegram message
        status_icon = "✅" if decision == "APPROVED" else "❌"
        updated_text = (
            f"{status_icon} *[Ticket {decision}]* Tool `{ticket.tool_id}`\n"
            f"• Decided by: `{reviewer_tag}` via Telegram\n"
            f"• Policy: `{policy_mode}`\n"
            f"• Resolved at: `{ticket.resolved_at.strftime('%Y-%m-%d %H:%M:%S UTC')}`"
        )
        await self._edit_message_text(chat_id, message_id, updated_text)

        return {
            "status": "RESOLVED",
            "ticket_id": str(ticket.id),
            "decision": decision,
            "policy_mode": policy_mode,
        }

    async def _answer_callback(self, callback_query_id: str, text: str) -> None:
        creds = await self.get_credentials()
        if not creds["token"] or not callback_query_id:
            return
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{creds['token']}/answerCallbackQuery",
                    json={"callback_query_id": callback_query_id, "text": text},
                )
        except Exception:
            pass

    async def _edit_message_text(self, chat_id: str, message_id: int, text: str) -> None:
        creds = await self.get_credentials()
        if not creds["token"] or not chat_id or not message_id:
            return
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{creds['token']}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "text": text,
                        "parse_mode": "Markdown",
                    },
                )
        except Exception:
            pass


class DiscordChannel(BaseChannel):
    """Discord Webhook Channel Gateway for Rich Embed Alerts."""

    async def get_credentials(self) -> Dict[str, Any]:
        redis_client = await get_redis()
        webhook = await redis_client.get("channel:discord:webhook") or os.getenv("DISCORD_WEBHOOK_URL", "")
        en_raw = await redis_client.get("channel:discord:enabled")
        enabled = (str(en_raw).lower() in ("true", "1", "none")) if en_raw is not None else bool(webhook)
        return {"webhook": str(webhook).strip() if webhook else "", "enabled": enabled}

    async def send_text(self, text: str) -> Dict[str, Any]:
        creds = await self.get_credentials()
        if not creds["webhook"] or not creds["enabled"]:
            return {"status": "SKIPPED", "reason": "not configured or disabled"}

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.post(creds["webhook"], json={"content": text[:1900]})
                return {"status": "DELIVERED", "http_code": res.status_code}
        except Exception as exc:
            logger.warning("Discord send_text failed: %s", exc)
            return {"status": "ERROR", "error": str(exc)}

    async def send_hitl_notification(self, ticket: HITLTicketModel) -> Dict[str, Any]:
        creds = await self.get_credentials()
        if not creds["webhook"] or not creds["enabled"]:
            return {"status": "SKIPPED", "reason": "not configured or disabled"}

        embed = {
            "title": "🔒 Human-in-the-Loop Gate Required",
            "description": f"Agent `@{ticket.agent_id}` requested protected tool `{ticket.tool_id}`.",
            "color": 0xF59E0B,  # Amber
            "fields": [
                {"name": "Pipeline", "value": f"`{ticket.workflow_id}`", "inline": True},
                {"name": "Gate Type", "value": f"`{ticket.gate_type}`", "inline": True},
                {"name": "Ticket ID", "value": f"`{ticket.id}`", "inline": False},
            ],
            "footer": {"text": "Review and approve in Blackboard Web Cockpit"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if ticket.tool_arguments and "command" in ticket.tool_arguments:
            embed["fields"].append(
                {"name": "Command", "value": f"```bash\n{ticket.tool_arguments['command']}\n```"}
            )

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                res = await client.post(
                    creds["webhook"],
                    json={
                        "content": "⚠️ **[Blackboard Alert]** Operator approval required for agent execution.",
                        "embeds": [embed],
                    },
                )
                return {"status": "DELIVERED", "http_code": res.status_code}
        except Exception as exc:
            logger.warning("Discord send_hitl_notification failed: %s", exc)
            return {"status": "ERROR", "error": str(exc)}


class ChannelGateway:
    """
    Unified Channel Gateway Service (SOLID: Dependency Inversion & Centralized Facade).
    Coordinates Telegram, Discord, and Custom Inbound Webhooks.
    """

    def __init__(self):
        self.telegram = TelegramChannel()
        self.discord = DiscordChannel()

    async def get_status(self) -> Dict[str, Any]:
        tg = await self.telegram.get_credentials()
        dc = await self.discord.get_credentials()
        return {
            "telegram": {
                "enabled": tg["enabled"],
                "configured": bool(tg["token"] and tg["chat_id"]),
                "bot_token_masked": f"{tg['token'][:5]}...{tg['token'][-4:]}" if len(tg["token"]) > 10 else None,
                "chat_id_masked": f"***{tg['chat_id'][-4:]}" if len(tg["chat_id"]) > 4 else tg["chat_id"] or None,
                "webhook_path": "/api/v1/channels/telegram/webhook",
                "cockpit_url": tg["cockpit_url"],
            },
            "discord": {
                "enabled": dc["enabled"],
                "configured": bool(dc["webhook"]),
                "webhook_url_masked": f"{dc['webhook'][:25]}...{dc['webhook'][-6:]}" if len(dc["webhook"]) > 35 else None,
                "inbound_path": "/api/v1/channels/message",
            },
            "easy_chat": {
                "endpoint": "/api/v1/channels/message",
                "supported_actions": [
                    "steer: <directive>",
                    "run workflow",
                    "inspect state",
                    "prune blackboard",
                ],
            },
        }

    async def update_config(self, payload: ChannelConfigRequest) -> Dict[str, Any]:
        redis_client = await get_redis()
        if payload.telegram_bot_token is not None:
            await redis_client.set("channel:telegram:token", payload.telegram_bot_token.strip())
        if payload.telegram_chat_id is not None:
            await redis_client.set("channel:telegram:chat_id", payload.telegram_chat_id.strip())
        if payload.telegram_enabled is not None:
            await redis_client.set("channel:telegram:enabled", "true" if payload.telegram_enabled else "false")

        if payload.discord_webhook_url is not None:
            await redis_client.set("channel:discord:webhook", payload.discord_webhook_url.strip())
        if payload.discord_enabled is not None:
            await redis_client.set("channel:discord:enabled", "true" if payload.discord_enabled else "false")

        return await self.get_status()

    async def notify_text(self, text: str) -> Dict[str, Any]:
        """Broadcast text to all configured external channels."""
        results = {}
        results["telegram"] = await self.telegram.send_text(text)
        results["discord"] = await self.discord.send_text(text)
        return results

    async def notify_hitl_ticket(self, ticket: HITLTicketModel) -> Dict[str, Any]:
        """Send rich interactive HITL ticket notification to Telegram (with buttons) and Discord (with embed)."""
        results = {}
        results["telegram"] = await self.telegram.send_hitl_notification(ticket)
        results["discord"] = await self.discord.send_hitl_notification(ticket)
        return results

    async def process_telegram_webhook(
        self, request_data: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Handle inbound updates from Telegram:
        1. callback_query: User tapped an inline button ([Approve Once], [Always Allow], [Reject]).
        2. message: User sent a command (/status, /cockpit, /approvals, /steer) or chat message.
        """
        from app.concierge import concierge_agent

        # 1. Inline Button Callback Query
        if "callback_query" in request_data:
            return await self.telegram.handle_callback_query(
                request_data["callback_query"], session
            )

        # 2. Inbound Message
        message = request_data.get("message", {})
        text = str(message.get("text", "")).strip()
        chat_id = str(message.get("chat", {}).get("id", ""))
        from_user = message.get("from", {}).get("username") or str(
            message.get("from", {}).get("id", "operator")
        )

        if not text or not chat_id:
            return {"status": "ok", "reason": "empty_message"}

        creds = await self.telegram.get_credentials()
        token = creds["token"]

        # Command: /start -> Auto-register chat_id and send welcome guide
        if text.startswith("/start"):
            redis_client = await get_redis()
            await redis_client.set("channel:telegram:chat_id", chat_id)
            await redis_client.set("channel:telegram:enabled", "true")

            cockpit_url = creds["cockpit_url"]
            is_https = cockpit_url.startswith("https://")
            keyboard = [
                [
                    {
                        "text": "🌐 Launch Blackboard Web Cockpit",
                        "web_app": {"url": cockpit_url} if is_https else {"url": cockpit_url},
                    }
                ]
            ]
            welcome_text = (
                f"👋 *Welcome to Multi-Agent Blackboard Cockpit!*\n\n"
                f"✅ Your Telegram Chat ID (`{chat_id}`) has been **automatically registered**!\n\n"
                f"• Real-time HITL gate alerts will appear here with 1-tap `[Approve]` / `[Reject]` buttons.\n"
                f"• Type `/status` to see running agents and pending gates.\n"
                f"• Type `/approvals` to review pending Human-in-the-Loop tickets.\n"
                f"• Type `/cockpit` to launch the mobile Web Cockpit.\n"
                f"• Or simply reply with any instruction to steer agents and query platform state!"
            )
            if token:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": welcome_text,
                                "parse_mode": "Markdown",
                                "reply_markup": {"inline_keyboard": keyboard},
                            },
                        )
                except Exception as e:
                    logger.warning("Telegram /start reply failed: %s", e)
            return {"status": "ok", "command": "/start", "chat_id": chat_id}

        # Command: /approvals -> List pending HITL tickets with inline buttons
        if text.startswith("/approvals"):
            hitl_stmt = (
                select(HITLTicketModel)
                .where(HITLTicketModel.status == "PENDING")
                .order_by(HITLTicketModel.created_at.desc())
            )
            res_hitl = await session.execute(hitl_stmt)
            tickets = res_hitl.scalars().all()
            if not tickets:
                reply_text = "🎉 *No Pending Approvals*\nAll Human-in-the-Loop gates are currently clear!"
                if token:
                    try:
                        async with httpx.AsyncClient(timeout=5.0) as client:
                            await client.post(
                                f"https://api.telegram.org/bot{token}/sendMessage",
                                json={"chat_id": chat_id, "text": reply_text, "parse_mode": "Markdown"},
                            )
                    except Exception as e:
                        logger.warning("Telegram /approvals reply failed: %s", e)
                return {"status": "ok", "command": "/approvals", "pending": 0}

            for t in tickets[:5]:
                await self.telegram.send_hitl_notification(t)
            return {"status": "ok", "command": "/approvals", "pending": len(tickets)}

        # Command: /cockpit -> Send WebApp inline button
        if text.startswith("/cockpit"):
            cockpit_url = creds["cockpit_url"]
            is_https = cockpit_url.startswith("https://")
            keyboard = [
                [
                    {
                        "text": "🌐 Launch Blackboard Web Cockpit",
                        "web_app": {"url": cockpit_url} if is_https else {"url": cockpit_url},
                    }
                ]
            ]
            reply_text = "🚀 *Multi-Agent Blackboard Cockpit*\nTap below to open the mobile cockpit interface:"
            if token:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": reply_text,
                                "parse_mode": "Markdown",
                                "reply_markup": {"inline_keyboard": keyboard},
                            },
                        )
                except Exception as e:
                    logger.warning("Telegram /cockpit reply failed: %s", e)
            return {"status": "ok", "command": "/cockpit"}

        # Command: /status -> Overview of agents, tools, workflows, HITL
        if text.startswith("/status"):
            # Query counts
            agents_stmt = select(AgentModel)
            res_ag = await session.execute(agents_stmt)
            agent_names = [a.id for a in res_ag.scalars().all()]

            hitl_stmt = select(HITLTicketModel).where(HITLTicketModel.status == "PENDING")
            res_hitl = await session.execute(hitl_stmt)
            pending_count = len(res_hitl.scalars().all())

            status_text = (
                f"📊 *Multi-Agent Blackboard Status*\n\n"
                f"• *Active Agents ({len(agent_names)}):* {', '.join(agent_names)}\n"
                f"• *Pending HITL Approvals:* `{pending_count}`\n"
                f"• *UTC Time:* `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}`\n\n"
                f"Use `/approvals` to review gates or send any instruction to steer."
            )
            if token:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": status_text,
                                "parse_mode": "Markdown",
                            },
                        )
                except Exception as e:
                    logger.warning("Telegram /status reply failed: %s", e)
            return {"status": "ok", "command": "/status"}

        # Default: Route to Concierge Admin Agent
        concierge_res = await concierge_agent.handle_chat(
            session,
            ConciergeChatRequest(
                message=text,
                workflow_id="code_review_pipeline",
            ),
        )

        if token:
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": concierge_res.reply[:4000],
                        },
                    )
            except Exception as e:
                logger.warning("Telegram chat reply failed: %s", e)

        return {"status": "ok", "reply": concierge_res.reply}

    async def start_telegram_polling(self) -> None:
        """
        Background long-polling loop for Telegram updates.
        Allows local/homelab nodes to receive commands and inline button callbacks
        without needing public HTTPS webhook domains or port forwarding.
        """
        last_offset = 0
        logger.info("Starting Telegram background long-polling service...")
        while True:
            try:
                creds = await self.telegram.get_credentials()
                token = creds["token"]
                enabled = creds["enabled"]

                # Skip polling if not configured, disabled, or using a test mock token
                if not token or not enabled or token.startswith("mock-"):
                    await asyncio.sleep(4)
                    continue

                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.get(
                        f"https://api.telegram.org/bot{token}/getUpdates",
                        params={"offset": last_offset, "timeout": 20},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        updates = data.get("result", [])
                        for update in updates:
                            update_id = update.get("update_id", 0)
                            last_offset = max(last_offset, update_id + 1)
                            async with AsyncSessionLocal() as session:
                                try:
                                    await self.process_telegram_webhook(update, session)
                                except Exception as e:
                                    logger.warning("Error processing telegram update %s: %s", update_id, e)
                    else:
                        await asyncio.sleep(5)
            except asyncio.CancelledError:
                logger.info("Telegram background polling stopped.")
                break
            except Exception as exc:
                logger.debug("Telegram polling transient exception: %s", exc)
                await asyncio.sleep(5)


# Singleton Instance
channel_gateway = ChannelGateway()
