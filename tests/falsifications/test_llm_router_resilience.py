import time
from typing import Any, Dict, List
import httpx
import pytest

from app.runner import StatelessAgentRunner


class MockHTTPClient:
    """Deterministic async HTTP client simulator for testing rate limits and slow endpoints."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: List[Dict[str, Any]] = []

    async def post(
        self, url: str, json: Dict[str, Any] = None, headers: Dict[str, str] = None, timeout: float = 30.0
    ) -> httpx.Response:
        self.calls.append(
            {"url": url, "json": json, "headers": headers or {}, "timeout": timeout}
        )
        return await self.handler(url, json, headers, timeout)


@pytest.mark.asyncio
async def test_falsify_slow_local_endpoint_stalls_worker_and_fails_over_to_nim(
    fake_redis,
):
    """
    Falsifier 6A (Slow Endpoint Circuit Breaker & NIM Escalation):
    Hypothesis: If a local LAN endpoint (e.g. CPU Ollama or sleeping Laptop node) times out,
    the worker places it into a Redis cooldown (`llm:cooldown:*`) and seamlessly fails over
    to the next healthy tier (Fast Local LAN or NVIDIA NIM) without stalling future turns.
    """
    await fake_redis.set("llm:config:nim_api_key", "nvapi-test-live-key-123456")
    runner = StatelessAgentRunner(worker_id="worker-resilience-1", redis_client=fake_redis)

    async def simulate_slow_local_then_fast_nim(url, payload, headers, timeout):
        if "192.168.1.150:11434" in url:
            raise httpx.ReadTimeout("Simulated slow CPU Ollama timeout")
        if "192.168.1.180:8081" in url:
            raise httpx.ConnectTimeout("Simulated local node busy")
        if "integrate.api.nvidia.com" in url:
            assert headers.get("Authorization") == "Bearer nvapi-test-live-key-123456"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "async def rate_limit(): return True  # Synthesized via NVIDIA NIM"
                            }
                        }
                    ]
                },
            )
        return httpx.Response(500)

    mock_client = MockHTTPClient(simulate_slow_local_then_fast_nim)
    inference_cfg = {
        "provider": "ollama",
        "model_name": "qwen2.5-coder:0.5b",
        "base_url": "http://192.168.1.150:11434",
        "timeout_seconds": 5.0,
    }
    task = {"task_id": "t-nim-1", "agent_id": "coder", "entry_type": "code", "prompt": "Write limiter"}

    output, _, source = await runner._query_inference_endpoint(
        client=mock_client,
        inference_config=inference_cfg,
        system_prompt="You are a coder.",
        user_message="Write limiter",
        allowed_tools=[],
        task=task,
    )

    assert "Synthesized via NVIDIA NIM" in output
    assert source.startswith("live:nvidia_nim:")
    assert "integrate.api.nvidia.com" in source

    # Verify the slow local endpoint was placed into Redis cooldown so the next turn skips it immediately
    ollama_ep_key = runner._endpoint_key("ollama", "http://192.168.1.150:11434", "qwen2.5-coder:0.5b")
    cooldown_val = await fake_redis.get(f"llm:cooldown:{ollama_ep_key}")
    assert cooldown_val is not None
    assert "timeout" in cooldown_val

    # Run a second turn: it MUST skip the cooled-down local endpoints without calling them again!
    mock_client.calls.clear()
    output2, _, source2 = await runner._query_inference_endpoint(
        client=mock_client,
        inference_config=inference_cfg,
        system_prompt="You are a coder.",
        user_message="Write limiter 2",
        allowed_tools=[],
        task=task,
    )
    assert len(mock_client.calls) == 1
    assert "integrate.api.nvidia.com" in mock_client.calls[0]["url"]
    assert source2.startswith("live:nvidia_nim:")


@pytest.mark.asyncio
async def test_falsify_nvidia_nim_429_rate_limit_drops_task_instead_of_using_local_lan(
    fake_redis,
):
    """
    Falsifier 6B (NVIDIA NIM HTTP 429 Rate-Limit & Local LAN Fallback):
    Hypothesis: When NVIDIA NIM returns HTTP 429 Too Many Requests (or hits the proactive
    RPM cap), the worker records the `Retry-After` cooldown in Redis and automatically
    falls back to the free local LAN cluster (`192.168.1.180:8081/v1`) with zero task loss.
    """
    await fake_redis.set("llm:config:nim_api_key", "nvapi-rate-limited-key")
    runner = StatelessAgentRunner(worker_id="worker-resilience-2", redis_client=fake_redis)

    async def simulate_nim_429_then_local_lan_ok(url, payload, headers, timeout):
        if "integrate.api.nvidia.com" in url:
            return httpx.Response(
                429,
                headers={"Retry-After": "45"},
                json={"error": "Rate limit exceeded (40 RPM free tier)"},
            )
        if "192.168.1.180:8081" in url:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": " Verified locally via CT118 qwen3-4b after NIM 429!"
                            }
                        }
                    ]
                },
            )
        return httpx.Response(500)

    mock_client = MockHTTPClient(simulate_nim_429_then_local_lan_ok)
    inference_cfg = {
        "provider": "nvidia_nim",
        "model_name": "meta/llama-3.3-70b-instruct",
        "base_url": "https://integrate.api.nvidia.com/v1",
    }
    task = {"task_id": "t-429-1", "agent_id": "architect", "entry_type": "spec", "prompt": "Design spec"}

    output, _, source = await runner._query_inference_endpoint(
        client=mock_client,
        inference_config=inference_cfg,
        system_prompt="You are an architect.",
        user_message="Design spec",
        allowed_tools=[],
        task=task,
    )

    assert "Verified locally via CT118 qwen3-4b after NIM 429!" in output
    assert source == "live:openai:qwen3-4b@http://192.168.1.180:8081/v1"

    nim_ep_key = runner._endpoint_key(
        "nvidia_nim", "https://integrate.api.nvidia.com/v1", "meta/llama-3.3-70b-instruct"
    )
    nim_cd = await fake_redis.get(f"llm:cooldown:{nim_ep_key}")
    assert nim_cd == "rate_limited_429 (retry_after=45s)"


@pytest.mark.asyncio
async def test_proactive_nim_rpm_token_bucket_routes_overflow_to_local_lan(
    fake_redis,
):
    """
    Falsifier 6C (Proactive RPM Guard):
    When the current minute's NIM request counter reaches `nim_max_rpm`, the worker
    proactively skips NVIDIA NIM before making an HTTP request and routes straight
    to the free local LAN endpoint.
    """
    await fake_redis.set("llm:config:nim_api_key", "nvapi-quota-key")
    await fake_redis.set("llm:config:nim_max_rpm", "5")
    minute_bucket = int(time.time() // 60)
    await fake_redis.set(f"llm:nim:rpm:{minute_bucket}", "5")

    runner = StatelessAgentRunner(worker_id="worker-resilience-3", redis_client=fake_redis)

    async def handler(url, payload, headers, timeout):
        if "integrate.api.nvidia.com" in url:
            pytest.fail("Should NOT have called NVIDIA NIM when proactive RPM cap was reached!")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Handled by free local LAN endpoint"}}]},
        )

    mock_client = MockHTTPClient(handler)
    inference_cfg = {
        "provider": "nvidia_nim",
        "model_name": "meta/llama-3.3-70b-instruct",
    }
    task = {"task_id": "t-rpm-1", "agent_id": "sec-auditor", "entry_type": "critique"}

    output, _, source = await runner._query_inference_endpoint(
        client=mock_client,
        inference_config=inference_cfg,
        system_prompt="Auditor",
        user_message="Audit",
        allowed_tools=[],
        task=task,
    )

    assert output == "Handled by free local LAN endpoint"
    assert source == "live:openai:qwen3-4b@http://192.168.1.180:8081/v1"
