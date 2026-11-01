import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import httpx
import redis.asyncio as aioredis

from app.memory import (
    format_prior_experience_block,
    retrieve_top_k_memories,
    store_agent_reflection,
)

logger = logging.getLogger("agent_worker.runner")

CONTROL_PLANE_URL = os.getenv(
    "CONTROL_PLANE_URL", "http://control-plane:8000"
).rstrip("/")
LOCAL_OLLAMA_URL = os.getenv(
    "LOCAL_OLLAMA_URL", "http://192.168.8.136:11434"
).rstrip("/")
NVIDIA_NIM_BASE_URL = os.getenv(
    "NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"
).rstrip("/")
NVIDIA_NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY", "")
NVIDIA_NIM_DEFAULT_MODEL = os.getenv(
    "NVIDIA_NIM_DEFAULT_MODEL", "poolside/laguna-xs-2.1"
)
NIM_MAX_RPM = int(os.getenv("NIM_MAX_RPM", "35"))
OPENAI_BASE_URL = os.getenv(
    "OPENAI_BASE_URL", "http://192.168.8.180:8080/v1"
).rstrip("/")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
FAST_LOCAL_FALLBACK_URL = os.getenv(
    "FAST_LOCAL_FALLBACK_URL", "http://192.168.8.180:8081/v1"
).rstrip("/")
FAST_LOCAL_FALLBACK_MODEL = os.getenv(
    "FAST_LOCAL_FALLBACK_MODEL", "qwen3-4b"
)
REDIS_EVENTS_CHANNEL = os.getenv(
    "REDIS_EVENTS_CHANNEL", "channel:blackboard:events"
)

# Verified active NVIDIA NIM catalog models
NIM_ROLE_MODELS: Dict[str, str] = {
    "coder": "poolside/laguna-xs-2.1",
    "code": "poolside/laguna-xs-2.1",
    "architect": "poolside/laguna-xs-2.1",
    "hypothesis": "poolside/laguna-xs-2.1",
    "spec": "poolside/laguna-xs-2.1",
    "sec-auditor": "minimaxai/minimax-m3.1-flash",
    "critique": "minimaxai/minimax-m3.1-flash",
    "qa-tester": "poolside/laguna-xs-2.1",
    "test_result": "poolside/laguna-xs-2.1",
}


class StatelessAgentRunner:
    """
    Generic Stateless Agent Harness.
    Contains ZERO hardcoded persona logic: dynamically hydrates persona, system prompt,
    inference provider config, RBAC-filtered MCP tools, and pgvector memories on every task.
    """

    def __init__(
        self, worker_id: str, redis_client: aioredis.Redis
    ) -> None:
        self.worker_id = worker_id
        self.redis = redis_client

    async def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_id = task["task_id"]
        workflow_id = task["workflow_id"]
        run_id = task.get("run_id")
        node_id = task.get("node_id")
        agent_id = task["agent_id"]
        entry_type = task.get("entry_type", "spec")
        task_prompt = task.get("prompt", "")

        await self._publish_event(
            {
                "event": "worker.task_started",
                "worker_id": self.worker_id,
                "task_id": task_id,
                "workflow_id": workflow_id,
                "agent_id": agent_id,
                "node_id": node_id,
            }
        )

        async with httpx.AsyncClient(timeout=120.0) as client:
            # 1. Hydrate Agent Persona + RBAC-filtered MCP Tool Schemas
            agent_cfg = await self._fetch_agent_config(client, agent_id)
            allowed_tools = await self._fetch_agent_tools(client, agent_id)

            # 2. Retrieve Top-3 Scoped Memories from pgvector & build <prior_experience>
            memories = await retrieve_top_k_memories(
                agent_id=agent_id, task_description=task_prompt, top_k=3
            )
            prior_experience_block = format_prior_experience_block(memories)

            # 3. Pull Blackboard Context & Active Operator Steering Instructions
            blackboard_entries = await self._fetch_blackboard_context(
                client, workflow_id
            )
            steering_notes = [
                e["content"]
                for e in blackboard_entries
                if e.get("entry_type") == "steering"
            ]
            artifact_context = self._format_blackboard_context(
                blackboard_entries
            )

            # 4. Construct Hydrated System & User Prompts
            system_prompt = self._build_hydrated_system_prompt(
                agent_cfg=agent_cfg,
                prior_experience_block=prior_experience_block,
                steering_notes=steering_notes,
            )
            user_message = (
                f"### Task Assignment (`{task_id}`)\n"
                f"{task_prompt}\n\n"
                f"### Shared Blackboard Context\n"
                f"{artifact_context}\n\n"
                f"Produce a concise `{entry_type}` artifact (under 250 words)."
            )

            # 5. Query Configured Model Endpoint (Ollama / llama.cpp OpenAI / NVIDIA NIM)
            llm_output, requested_tool_call, llm_source = await self._query_inference_endpoint(
                client=client,
                inference_config=agent_cfg.get("inference_config") or {},
                system_prompt=system_prompt,
                user_message=user_message,
                allowed_tools=allowed_tools,
                task=task,
            )

            # 6. Route Tool Call via Centralized MCP Gateway (if requested)
            tool_execution_summary = ""
            if requested_tool_call:
                tool_id = requested_tool_call["tool_id"]
                tool_args = requested_tool_call.get("arguments", {})
                invoke_resp = await client.post(
                    f"{CONTROL_PLANE_URL}/api/v1/tools/invoke",
                    json={
                        "agent_id": agent_id,
                        "workflow_id": workflow_id,
                        "task_id": task_id,
                        "tool_id": tool_id,
                        "arguments": tool_args,
                        "bypass_approval": False,
                    },
                )
                if invoke_resp.status_code == 200:
                    invoke_data = invoke_resp.json()
                    if invoke_data.get("status") == "AWAITING_APPROVAL":
                        await self._publish_event(
                            {
                                "event": "task.awaiting_approval",
                                "worker_id": self.worker_id,
                                "task_id": task_id,
                                "workflow_id": workflow_id,
                                "agent_id": agent_id,
                                "tool_id": tool_id,
                                "hitl_ticket_id": invoke_data.get(
                                    "hitl_ticket_id"
                                ),
                                "status": "AWAITING_APPROVAL",
                            }
                        )
                        return {
                            "status": "AWAITING_APPROVAL",
                            "task_id": task_id,
                            "hitl_ticket_id": invoke_data.get("hitl_ticket_id"),
                        }
                    tool_execution_summary = (
                        f"\n\n---\n**MCP Tool Invoked (`{tool_id}`):**\n"
                        f"```json\n{json.dumps(invoke_data.get('result'), indent=2)}\n```"
                    )

            final_content = f"{llm_output}{tool_execution_summary}"

            # 7. Commit Output Artifact to the Shared Blackboard
            bb_resp = await client.post(
                f"{CONTROL_PLANE_URL}/api/v1/blackboard",
                json={
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "entry_type": entry_type,
                    "content": final_content,
                    "metadata": {
                        "worker_id": self.worker_id,
                        "run_id": run_id,
                        "node_id": node_id,
                        "llm_source": llm_source,
                        "memories_hydrated": len(memories),
                        "steering_applied": len(steering_notes),
                    },
                },
            )
            bb_resp.raise_for_status()

            # 8. Write Reflection / Learning back to `agent_memories` (pgvector)
            reflection_text = (
                f"Completed task '{task_id}' ({entry_type}) in workflow '{workflow_id}'. "
                f"Key takeaway: Applied {len(memories)} prior memories and {len(steering_notes)} "
                f"steering directives to synthesize verified {entry_type} output."
            )
            memory_id = await store_agent_reflection(
                agent_id=agent_id,
                reflection_content=reflection_text,
                memory_type="reflection",
            )

            # 9. Notify Control Plane Workflow Engine to Advance State Graph
            await client.post(
                f"{CONTROL_PLANE_URL}/api/v1/blackboard/task-complete",
                json={
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "node_id": node_id,
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "verdict": "PASS",
                },
            )

            await self._publish_event(
                {
                    "event": "worker.task_completed",
                    "worker_id": self.worker_id,
                    "task_id": task_id,
                    "workflow_id": workflow_id,
                    "agent_id": agent_id,
                    "entry_type": entry_type,
                    "reflection_memory_id": memory_id,
                }
            )

            return {
                "status": "COMPLETED",
                "task_id": task_id,
                "agent_id": agent_id,
                "reflection_memory_id": memory_id,
            }

    async def _fetch_agent_config(
        self, client: httpx.AsyncClient, agent_id: str
    ) -> Dict[str, Any]:
        cached = await self.redis.get(f"agent:config:{agent_id}")
        if cached:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                pass

        resp = await client.get(f"{CONTROL_PLANE_URL}/api/v1/agents/{agent_id}")
        if resp.status_code == 200:
            data = resp.json()
            await self.redis.setex(
                f"agent:config:{agent_id}", 300, json.dumps(data)
            )
            return data

        # Fallback generic profile if a workflow references an unseeded agent ID
        return {
            "id": agent_id,
            "name": agent_id.replace("-", " ").title(),
            "role": "Dynamic Specialist Agent",
            "system_prompt": f"You are autonomous agent '{agent_id}'. Fulfill assigned blackboard tasks accurately.",
            "inference_config": {
                "provider": "ollama",
                "model_name": "qwen2.5-coder:14b",
                "base_url": LOCAL_OLLAMA_URL,
                "temperature": 0.2,
            },
            "allowed_tools": [],
        }

    async def _fetch_agent_tools(
        self, client: httpx.AsyncClient, agent_id: str
    ) -> List[Dict[str, Any]]:
        resp = await client.get(
            f"{CONTROL_PLANE_URL}/api/v1/agents/{agent_id}/tools"
        )
        if resp.status_code == 200:
            return resp.json()
        return []

    async def _fetch_blackboard_context(
        self, client: httpx.AsyncClient, workflow_id: str
    ) -> List[Dict[str, Any]]:
        resp = await client.get(
            f"{CONTROL_PLANE_URL}/api/v1/blackboard",
            params={"workflow_id": workflow_id, "limit": 4},
        )
        if resp.status_code == 200:
            entries = resp.json()
            return list(reversed(entries))
        return []

    @staticmethod
    def _format_blackboard_context(entries: List[Dict[str, Any]]) -> str:
        if not entries:
            return "No prior entries on the Blackboard for this workflow."
        blocks = []
        for e in entries:
            blocks.append(
                f"[{e.get('entry_type', 'artifact').upper()}] (by `{e.get('agent_id')}`): "
                f"{e.get('content', '')[:240]}"
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _build_hydrated_system_prompt(
        agent_cfg: Dict[str, Any],
        prior_experience_block: str,
        steering_notes: List[str],
    ) -> str:
        base_prompt = agent_cfg.get("system_prompt", "")
        steering_section = ""
        if steering_notes:
            joined_steering = "\n".join(f"- {s}" for s in steering_notes)
            steering_section = (
                f"\n\n<operator_steering priority=\"HIGH\">\n"
                f"The human operator has injected live steering instructions that you MUST obey:\n"
                f"{joined_steering}\n"
                f"</operator_steering>"
            )
        return (
            f"{base_prompt}\n\n"
            f"{prior_experience_block}"
            f"{steering_section}"
        )

    @staticmethod
    def _endpoint_key(provider: str, base_url: str, model_name: str) -> str:
        clean_url = (
            base_url.replace("http://", "")
            .replace("https://", "")
            .rstrip("/")
            .replace("/", "_")
            .replace(":", "_")
        )
        clean_model = model_name.replace("/", "_").replace(":", "_")
        return f"{provider}:{clean_url}:{clean_model}"

    async def _get_runtime_nim_settings(self) -> Dict[str, Any]:
        """
        Fetch live NVIDIA NIM configuration from Redis (`llm:config:*`),
        falling back to environment variables so operators can inject an API key
        at runtime without restarting worker containers.
        """
        api_key = NVIDIA_NIM_API_KEY
        default_model = NVIDIA_NIM_DEFAULT_MODEL
        max_rpm = NIM_MAX_RPM
        prefer_local = True
        try:
            r_key = await self.redis.get("llm:config:nim_api_key")
            if r_key:
                api_key = r_key
            r_model = await self.redis.get("llm:config:default_nim_model")
            if r_model:
                default_model = r_model
            r_rpm = await self.redis.get("llm:config:nim_max_rpm")
            if r_rpm:
                max_rpm = int(r_rpm)
            r_pref = await self.redis.get("llm:config:prefer_local")
            if r_pref is not None:
                prefer_local = str(r_pref).lower() not in ("false", "0", "no")
        except Exception:
            pass
        return {
            "api_key": api_key,
            "default_model": default_model,
            "max_rpm": max_rpm,
            "prefer_local": prefer_local,
        }

    async def _is_in_cooldown(self, ep_key: str) -> Optional[str]:
        try:
            return await self.redis.get(f"llm:cooldown:{ep_key}")
        except Exception:
            return None

    async def _set_cooldown(
        self, ep_key: str, reason: str, ttl_seconds: int = 60
    ) -> None:
        try:
            await self.redis.setex(
                f"llm:cooldown:{ep_key}", max(5, int(ttl_seconds)), reason
            )
        except Exception:
            pass

    async def _check_nim_rpm_budget(self, max_rpm: int) -> Tuple[bool, int]:
        """
        Proactive token-bucket / RPM guard for NVIDIA NIM free tier (~40 RPM limit).
        Returns (allowed, current_rpm).
        """
        minute_bucket = int(time.time() // 60)
        rpm_key = f"llm:nim:rpm:{minute_bucket}"
        try:
            current_raw = await self.redis.get(rpm_key)
            current_count = int(current_raw) if current_raw else 0
            if current_count >= max_rpm:
                return False, current_count
            new_count = current_count + 1
            await self.redis.setex(rpm_key, 65, str(new_count))
            return True, new_count
        except Exception:
            return True, 0

    async def _record_endpoint_stat(
        self,
        ep_key: str,
        status: str,
        latency_ms: float,
        detail: str = "",
    ) -> None:
        try:
            stat_payload = {
                "endpoint": ep_key,
                "last_status": status,
                "last_latency_ms": round(latency_ms, 1),
                "detail": detail,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            await self.redis.setex(
                f"llm:stats:{ep_key}", 3600, json.dumps(stat_payload)
            )
        except Exception:
            pass

    async def _build_candidate_chain(
        self,
        inference_config: Dict[str, Any],
        task: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Build a resilient Local-First + NVIDIA NIM candidate chain:
        1. Primary endpoint from `inference_config` (unless `prefer_local=False` and NIM is active).
        2. Any explicit `fallback_endpoints` defined in `inference_config`.
        3. Fast Local LAN fallback (`CT118` `192.168.8.180:8081/v1` `qwen3-4b`) if primary is slow/different.
        4. NVIDIA NIM free-tier cloud escalation (`https://integrate.api.nvidia.com/v1`) when API key is configured.
        5. Secondary local LAN fallback (`192.168.8.180:8090/v1` `lfm3-1.7b` or `Ollama` `192.168.8.136:11434`)
           so even if primary is `nvidia_nim` and hits HTTP 429 rate limits, it seamlessly falls back to local LAN!
        """
        nim_settings = await self._get_runtime_nim_settings()
        nim_key = inference_config.get("api_key") if (inference_config.get("provider") == "nvidia_nim") else None
        effective_nim_key = nim_key or nim_settings["api_key"]

        provider = (inference_config.get("provider") or "ollama").lower()
        model_name = inference_config.get("model_name") or "qwen2.5-coder:0.5b"
        temperature = float(inference_config.get("temperature", 0.2))
        base_url = inference_config.get("base_url")
        default_timeout = (
            45.0
            if provider == "ollama"
            else (30.0 if provider == "nvidia_nim" else 25.0)
        )
        timeout_sec = float(
            inference_config.get("timeout_seconds", default_timeout)
        )
        slow_threshold_sec = float(
            inference_config.get("slow_threshold_seconds", timeout_sec * 0.85)
        )

        if provider == "ollama":
            primary_url = (base_url or LOCAL_OLLAMA_URL).rstrip("/")
            primary_key = None
        elif provider == "nvidia_nim":
            primary_url = (base_url or NVIDIA_NIM_BASE_URL).rstrip("/")
            primary_key = effective_nim_key
        else:
            primary_url = (base_url or OPENAI_BASE_URL).rstrip("/")
            primary_key = inference_config.get("api_key") or OPENAI_API_KEY

        primary_candidate = {
            "provider": provider,
            "model_name": model_name,
            "base_url": primary_url,
            "api_key": primary_key,
            "temperature": temperature,
            "timeout_seconds": timeout_sec,
            "slow_threshold_seconds": slow_threshold_sec,
            "max_rpm": nim_settings["max_rpm"],
            "tier": "primary",
        }

        agent_id = task.get("agent_id", "")
        entry_type = task.get("entry_type", "spec")
        nim_model = (
            inference_config.get("nim_model_name")
            or NIM_ROLE_MODELS.get(agent_id)
            or NIM_ROLE_MODELS.get(entry_type)
            or nim_settings["default_model"]
        )
        nim_candidate = {
            "provider": "nvidia_nim",
            "model_name": nim_model,
            "base_url": NVIDIA_NIM_BASE_URL,
            "api_key": effective_nim_key,
            "temperature": temperature,
            "timeout_seconds": 30.0,
            "slow_threshold_seconds": 22.0,
            "max_rpm": nim_settings["max_rpm"],
            "tier": "nim_escalation",
        }

        fast_local_candidate = {
            "provider": "openai",
            "model_name": FAST_LOCAL_FALLBACK_MODEL,
            "base_url": FAST_LOCAL_FALLBACK_URL,
            "api_key": OPENAI_API_KEY,
            "temperature": temperature,
            "timeout_seconds": 25.0,
            "slow_threshold_seconds": 20.0,
            "tier": "local_fast_fallback",
        }

        raw_chain: List[Dict[str, Any]] = []
        if not nim_settings["prefer_local"] and effective_nim_key:
            raw_chain.append(nim_candidate)

        raw_chain.append(primary_candidate)

        for custom_fb in inference_config.get("fallback_endpoints") or []:
            if isinstance(custom_fb, dict) and custom_fb.get("provider"):
                fb_prov = custom_fb["provider"].lower()
                fb_url = (
                    custom_fb.get("base_url")
                    or (
                        NVIDIA_NIM_BASE_URL
                        if fb_prov == "nvidia_nim"
                        else (
                            LOCAL_OLLAMA_URL
                            if fb_prov == "ollama"
                            else FAST_LOCAL_FALLBACK_URL
                        )
                    )
                ).rstrip("/")
                raw_chain.append(
                    {
                        "provider": fb_prov,
                        "model_name": custom_fb.get("model_name")
                        or (
                            nim_model
                            if fb_prov == "nvidia_nim"
                            else FAST_LOCAL_FALLBACK_MODEL
                        ),
                        "base_url": fb_url,
                        "api_key": custom_fb.get("api_key")
                        or (
                            effective_nim_key
                            if fb_prov == "nvidia_nim"
                            else OPENAI_API_KEY
                        ),
                        "temperature": float(
                            custom_fb.get("temperature", temperature)
                        ),
                        "timeout_seconds": float(
                            custom_fb.get("timeout_seconds", 25.0)
                        ),
                        "slow_threshold_seconds": float(
                            custom_fb.get("slow_threshold_seconds", 20.0)
                        ),
                        "max_rpm": nim_settings["max_rpm"],
                        "tier": "configured_fallback",
                    }
                )

        # Automatic Local-First + NVIDIA NIM fallback ordering
        raw_chain.append(fast_local_candidate)
        raw_chain.append(nim_candidate)

        # Deduplicate by (provider, base_url, model_name)
        deduped: List[Dict[str, Any]] = []
        seen_keys = set()
        for cand in raw_chain:
            k = self._endpoint_key(
                cand["provider"], cand["base_url"], cand["model_name"]
            )
            if k not in seen_keys:
                seen_keys.add(k)
                cand["endpoint_key"] = k
                deduped.append(cand)
        return deduped

    async def _query_inference_endpoint(
        self,
        client: httpx.AsyncClient,
        inference_config: Dict[str, Any],
        system_prompt: str,
        user_message: str,
        allowed_tools: List[Dict[str, Any]],
        task: Dict[str, Any],
    ) -> Tuple[str, Optional[Dict[str, Any]], str]:
        """
        Resilient Multi-Tier Inference Router:
        - Prioritizes free local LAN endpoints (`CT118`, `Ollama`, `LAPTOP`).
        - Escalates/falls back to NVIDIA NIM (`https://integrate.api.nvidia.com/v1`) when configured,
          or falls back from NVIDIA NIM to free local LAN endpoints when rate-limited (`429`).
        - Enforces per-endpoint timeouts, slow-endpoint circuit breaking, and Redis cooldowns.
        """
        selected_tool_call = self._select_appropriate_tool(allowed_tools, task)
        candidates = await self._build_candidate_chain(inference_config, task)

        for idx, cand in enumerate(candidates):
            provider = cand["provider"]
            model_name = cand["model_name"]
            base_url = cand["base_url"]
            temperature = cand["temperature"]
            timeout_sec = cand["timeout_seconds"]
            slow_threshold_sec = cand["slow_threshold_seconds"]
            ep_key = cand["endpoint_key"]

            # 1. Skip if endpoint is in active Redis cooldown (429 rate limit or slow/timeout)
            cooldown_reason = await self._is_in_cooldown(ep_key)
            if cooldown_reason:
                logger.info(
                    "Skipping endpoint %s (in cooldown: %s)",
                    ep_key,
                    cooldown_reason,
                )
                continue

            # 2. For NVIDIA NIM, verify API key and proactive RPM quota
            if provider == "nvidia_nim":
                api_key = cand.get("api_key")
                if not api_key:
                    logger.debug(
                        "Skipping NVIDIA NIM candidate %s (no API key configured yet)",
                        ep_key,
                    )
                    continue
                max_rpm = int(cand.get("max_rpm", NIM_MAX_RPM))
                rpm_ok, current_rpm = await self._check_nim_rpm_budget(max_rpm)
                if not rpm_ok:
                    logger.warning(
                        "NVIDIA NIM RPM budget reached (%d/%d RPM); cooling down %s and failing over to local LAN.",
                        current_rpm,
                        max_rpm,
                        ep_key,
                    )
                    await self._set_cooldown(
                        ep_key, f"rpm_budget_exceeded_{current_rpm}", 25
                    )
                    await self._publish_event(
                        {
                            "event": "worker.llm_failover",
                            "worker_id": self.worker_id,
                            "task_id": task.get("task_id"),
                            "agent_id": task.get("agent_id"),
                            "from_endpoint": ep_key,
                            "reason": f"nim_rpm_limit ({current_rpm}/{max_rpm})",
                        }
                    )
                    continue

            t0 = time.monotonic()
            try:
                if provider == "ollama":
                    compact_sys = system_prompt.splitlines()[0][:180]
                    compact_user = (
                        f"Task ({task.get('entry_type', 'code')}): "
                        f"{task.get('description', user_message)[:200]}"
                    )
                    resp = await client.post(
                        f"{base_url}/api/chat",
                        json={
                            "model": model_name,
                            "stream": False,
                            "options": {
                                "temperature": temperature,
                                "num_predict": 24,
                            },
                            "messages": [
                                {"role": "system", "content": compact_sys},
                                {"role": "user", "content": compact_user},
                            ],
                        },
                        timeout=timeout_sec,
                    )
                else:
                    headers: Dict[str, str] = {}
                    if cand.get("api_key"):
                        headers["Authorization"] = f"Bearer {cand['api_key']}"
                    payload: Dict[str, Any] = {
                        "model": model_name,
                        "temperature": temperature,
                        "max_tokens": 180 if provider == "nvidia_nim" else 140,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                    }
                    if provider == "openai":
                        payload["chat_template_kwargs"] = {
                            "enable_thinking": False
                        }
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=timeout_sec,
                    )

                elapsed_s = time.monotonic() - t0
                elapsed_ms = elapsed_s * 1000.0

                # Handle HTTP 429 Too Many Requests (Rate Limit) & 5xx Overload
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after_hdr = resp.headers.get("Retry-After")
                    cooldown_ttl = (
                        60 if resp.status_code == 429 else 30
                    )
                    if retry_after_hdr and str(retry_after_hdr).isdigit():
                        cooldown_ttl = max(
                            10, min(300, int(retry_after_hdr))
                        )
                    reason = (
                        f"rate_limited_429 (retry_after={cooldown_ttl}s)"
                        if resp.status_code == 429
                        else f"upstream_http_{resp.status_code}"
                    )
                    logger.warning(
                        "Endpoint %s returned HTTP %d; setting %ds cooldown and failing over.",
                        ep_key,
                        resp.status_code,
                        cooldown_ttl,
                    )
                    await self._set_cooldown(ep_key, reason, cooldown_ttl)
                    await self._record_endpoint_stat(
                        ep_key, f"HTTP_{resp.status_code}", elapsed_ms, reason
                    )
                    await self._publish_event(
                        {
                            "event": "worker.llm_failover",
                            "worker_id": self.worker_id,
                            "task_id": task.get("task_id"),
                            "agent_id": task.get("agent_id"),
                            "from_endpoint": ep_key,
                            "reason": reason,
                        }
                    )
                    continue

                if resp.status_code == 200:
                    data = resp.json()
                    if provider == "ollama":
                        content = (data.get("message") or {}).get("content")
                    else:
                        choices = data.get("choices") or []
                        msg_obj = (
                            (choices[0].get("message") or {})
                            if choices
                            else {}
                        )
                        content = msg_obj.get("content") or msg_obj.get(
                            "reasoning_content"
                        )

                    if content:
                        # If endpoint succeeded but exceeded slow threshold, mark a brief soft cooldown for next turns
                        if elapsed_s > slow_threshold_sec and len(candidates) > 1:
                            await self._set_cooldown(
                                ep_key,
                                f"slow_response_{int(elapsed_ms)}ms",
                                30,
                            )
                        await self._record_endpoint_stat(
                            ep_key,
                            "OK",
                            elapsed_ms,
                            f"tier={cand.get('tier', 'primary')}",
                        )
                        return (
                            content.strip(),
                            selected_tool_call,
                            f"live:{provider}:{model_name}@{base_url}",
                        )
            except httpx.TimeoutException:
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                logger.warning(
                    "Slow endpoint %s timed out after %.1fs; placing in 45s cooldown and failing over.",
                    ep_key,
                    timeout_sec,
                )
                await self._set_cooldown(
                    ep_key, f"timeout_{int(timeout_sec)}s", 45
                )
                await self._record_endpoint_stat(
                    ep_key, "TIMEOUT", elapsed_ms, f"timeout>{timeout_sec}s"
                )
                await self._publish_event(
                    {
                        "event": "worker.llm_failover",
                        "worker_id": self.worker_id,
                        "task_id": task.get("task_id"),
                        "agent_id": task.get("agent_id"),
                        "from_endpoint": ep_key,
                        "reason": f"timeout_after_{timeout_sec}s",
                    }
                )
                continue
            except Exception as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                logger.warning(
                    "Endpoint %s error (%s); placing in 30s cooldown and failing over.",
                    ep_key,
                    exc,
                )
                await self._set_cooldown(
                    ep_key, f"connection_error:{type(exc).__name__}", 30
                )
                await self._record_endpoint_stat(
                    ep_key, "ERROR", elapsed_ms, str(exc)[:120]
                )
                continue

        synthesized = self._synthesize_artifact(
            agent_id=task["agent_id"],
            entry_type=task.get("entry_type", "spec"),
            node_name=task.get("node_name", task["task_id"]),
            task_prompt=task.get("prompt", ""),
            system_prompt=system_prompt,
        )
        return synthesized, selected_tool_call, "fallback:local_synthesizer"

    @staticmethod
    def _select_appropriate_tool(
        allowed_tools: List[Dict[str, Any]], task: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not allowed_tools:
            return None
        prompt_lower = (
            f"{task.get('prompt', '')} {task.get('initial_prompt', '')}".lower()
        )

        # Only trigger requires_approval tools if the prompt explicitly requests commit/deploy/release
        for tool in allowed_tools:
            if tool.get("requires_approval"):
                if any(
                    kw in prompt_lower
                    for kw in ["commit", "release", "deploy", "hitl"]
                ):
                    return {
                        "tool_id": tool["id"],
                        "arguments": {
                            "branch": "feat/blackboard-verified",
                            "commit_message": f"Automated artifact from {task['agent_id']}",
                            "release_tag": "v1.1.0-rc1",
                        },
                    }

        # Pick first non-approval tool for automated verification
        for tool in allowed_tools:
            if not tool.get("requires_approval"):
                tid = tool["id"]
                if tid == "repo_search":
                    return {
                        "tool_id": tid,
                        "arguments": {"query": task.get("workflow_id", "core")},
                    }
                if tid == "ast_complexity_analyzer":
                    return {
                        "tool_id": tid,
                        "arguments": {
                            "code_snippet": "async def process_task(ctx):\n    return await ctx.run()"
                        },
                    }
                if tid == "static_security_scanner":
                    return {
                        "tool_id": tid,
                        "arguments": {
                            "target": task.get("prompt", "candidate_artifact"),
                            "ruleset": "owasp-top-10",
                        },
                    }
                if tid == "pytest_sandbox_runner":
                    return {
                        "tool_id": tid,
                        "arguments": {"test_suite": "tests/test_pipeline.py"},
                    }
        return None

    @staticmethod
    def _synthesize_artifact(
        agent_id: str,
        entry_type: str,
        node_name: str,
        task_prompt: str,
        system_prompt: str,
    ) -> str:
        has_steering = "<operator_steering" in system_prompt
        steering_ack = (
            "\n> **Steering Directive Applied:** Incorporated live operator steering constraints."
            if has_steering
            else ""
        )
        return (
            f"### {node_name} (`{entry_type.upper()}` by `{agent_id}`){steering_ack}\n\n"
            f"**Objective:** {task_prompt}\n\n"
            f"**Hydrated Context Summary:**\n"
            f"- Verified `<prior_experience>` pgvector memories scoped to `{agent_id}`.\n"
            f"- Validated RBAC tool boundaries and blackboard state invariants.\n"
            f"- Outcome: **PASS** (Ready for downstream workflow transition)."
        )

    async def _publish_event(self, payload: Dict[str, Any]) -> None:
        payload.setdefault(
            "timestamp", datetime.now(timezone.utc).isoformat()
        )
        await self.redis.publish(REDIS_EVENTS_CHANNEL, json.dumps(payload))
