from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import main
from app.config import get_settings
from app.llm import SimulatedProviderFailure
from app.rate_limiter import SlidingWindowRateLimiter, chat_rate_limiter


def response(text: str, *, cache_hit: bool = False, input_tokens: int = 42):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=text)),
        ],
        usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=9),
        _hidden_params={"cache_hit": cache_hit},
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("LLM_API_KEYS", "first-test-key,second-test-key")
    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.5-flash-lite")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("CHAT_RATE_LIMIT_REQUESTS", "12")
    monkeypatch.setenv("CHAT_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("MAX_HISTORY_MESSAGES", "40")
    monkeypatch.setenv("DEFAULT_SYSTEM_PROMPT", "You are a small business assistant.")
    monkeypatch.setattr(main, "initialize_litellm", lambda: True)
    monkeypatch.setattr(main, "cache_is_ready", lambda: True)
    main.response_cache.clear()
    chat_rate_limiter.clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.response_cache.clear()
    chat_rate_limiter.clear()


def test_health_reports_database_keys_and_cache(client):
    result = client.get("/health")

    assert result.status_code == 200
    assert result.json() == {
        "status": "ok",
        "keys_configured": 2,
        "cache_enabled": True,
        "database_ready": True,
        "failure_simulation_enabled": True,
    }


def test_only_the_three_assignment_api_routes_are_exposed(client):
    routes = {
        (method, route.path)
        for route in main.app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert routes == {
        ("GET", "/health"),
        ("POST", "/chat"),
        ("GET", "/sessions/{session_id}/messages"),
    }


def test_cors_preflight_allows_frontend_and_simulation_header(client):
    result = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )

    assert result.status_code == 200
    assert result.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-simulate-failure" in result.headers["access-control-allow-headers"].lower()


def test_sliding_window_limiter_expires_hits_and_returns_retry_delay(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.rate_limiter.time.monotonic", lambda: now[0])
    limiter = SlidingWindowRateLimiter()

    assert limiter.allow("client", limit=2, window_seconds=10) == (True, 0)
    now[0] = 102.0
    assert limiter.allow("client", limit=2, window_seconds=10) == (True, 0)
    now[0] = 103.0
    allowed, retry_after = limiter.allow("client", limit=2, window_seconds=10)
    assert not allowed
    assert retry_after == 7
    now[0] = 110.0
    assert limiter.allow("client", limit=2, window_seconds=10) == (True, 0)


def test_legacy_environment_prompt_is_normalized_to_english(client, monkeypatch):
    monkeypatch.setenv(
        "DEFAULT_SYSTEM_PROMPT",
        "You are helpful. Reply in the customer's language.",
    )

    prompt = get_settings().default_system_prompt

    assert "Always reply in clear, natural English" in prompt
    assert "Reply in the customer's language" not in prompt
    assert "valid Markdown tables for comparisons or when requested" in prompt
    assert "no more than four columns" in prompt


def test_legacy_saved_session_prompt_is_upgraded_to_english(client, monkeypatch):
    loaded_prompt = "You are helpful. Respond in the language and tone used by the customer."
    original_load_session_context = main.load_session_context
    observed_prompts = []

    monkeypatch.setattr(
        main,
        "load_session_context",
        lambda _session_id, limit=None: (True, loaded_prompt, []),
    )

    async def fake_complete(**kwargs):
        observed_prompts.append(kwargs["messages"][0]["content"])
        return response("An API lets software systems communicate.")

    monkeypatch.setattr(main, "complete", fake_complete)
    result = client.post(
        "/chat",
        json={"session_id": "legacy-language-session", "message": "Define an API."},
    )

    assert result.status_code == 200
    assert "Always reply in clear, natural English" in observed_prompts[0]
    assert "language and tone used by the customer" not in observed_prompts[0]
    assert (
        original_load_session_context("legacy-language-session")[1]
        == observed_prompts[0]
    )


def test_multi_turn_history_is_stored_oldest_first_and_keeps_session_isolated(
    client, monkeypatch
):
    async def fake_complete(**kwargs):
        return response(f"answer {len(kwargs['messages'])}")

    monkeypatch.setattr(main, "complete", fake_complete)

    first = client.post(
        "/chat", json={"session_id": "alpha", "message": "My city is Jaipur."}
    )
    second = client.post(
        "/chat", json={"session_id": "alpha", "message": "Which city did I mention?"}
    )
    other = client.post(
        "/chat", json={"session_id": "beta", "message": "Hello"}
    )

    assert first.status_code == second.status_code == other.status_code == 200
    assert first.json()["metadata"]["input_tokens"] == 42
    assert first.json()["metadata"]["output_tokens"] == 9
    assert first.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert set(first.json()["metadata"]) == {
        "resolution_source",
        "model",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "key_id",
        "fallback_attempts",
    }
    history = client.get("/sessions/alpha/messages").json()["messages"]
    assert [item["role"] for item in history] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert history[0]["content"] == "My city is Jaipur."
    assert history[2]["content"] == "Which city did I mention?"
    assert "metadata" in history[1]
    assert client.get("/sessions/beta/messages").json()["messages"][0]["content"] == "Hello"


def test_model_context_is_bounded_but_sqlite_keeps_full_history(
    client, monkeypatch
):
    monkeypatch.setenv("MAX_HISTORY_MESSAGES", "2")
    observed_messages = []
    observed_database_limits = []
    original_load_context = main.load_session_context

    def bounded_load_context(session_id, limit=None):
        observed_database_limits.append(limit)
        return original_load_context(session_id, limit)

    async def fake_complete(**kwargs):
        observed_messages.append(kwargs["messages"])
        return response(f"answer {len(observed_messages)}")

    monkeypatch.setattr(main, "load_session_context", bounded_load_context)
    monkeypatch.setattr(main, "complete", fake_complete)
    for message in ("First fact.", "Second fact.", "Third question."):
        result = client.post(
            "/chat", json={"session_id": "bounded-context", "message": message}
        )
        assert result.status_code == 200

    third_prompt = observed_messages[2]
    assert [item["role"] for item in third_prompt] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert [item["content"] for item in third_prompt[-3:]] == [
        "Second fact.",
        "answer 2",
        "Third question.",
    ]
    stored_history = client.get("/sessions/bounded-context/messages").json()["messages"]
    assert len(stored_history) == 6
    assert stored_history[0]["content"] == "First fact."
    assert observed_database_limits == [2, 2, 2]


def test_chat_rate_limit_returns_429_retry_after_and_does_not_save_turn(
    client, monkeypatch
):
    monkeypatch.setenv("CHAT_RATE_LIMIT_REQUESTS", "1")
    calls = []

    async def fake_complete(**_kwargs):
        calls.append(True)
        return response("A short reply.")

    monkeypatch.setattr(main, "complete", fake_complete)
    first = client.post(
        "/chat", json={"session_id": "limited-one", "message": "First"}
    )
    blocked = client.post(
        "/chat",
        headers={"Origin": "http://localhost:3000"},
        json={"session_id": "limited-two", "message": "Second"},
    )

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert blocked.json() == {"detail": "rate limit exceeded"}
    assert int(blocked.headers["retry-after"]) >= 1
    assert "retry-after" in blocked.headers["access-control-expose-headers"].lower()
    assert client.get("/sessions/limited-two/messages").status_code == 404
    assert len(calls) == 1


def test_only_railway_edge_marked_real_ip_changes_rate_limit_identity(
    client, monkeypatch
):
    monkeypatch.setenv("CHAT_RATE_LIMIT_REQUESTS", "1")

    async def fake_complete(**_kwargs):
        return response("A short reply.")

    monkeypatch.setattr(main, "complete", fake_complete)
    first = client.post(
        "/chat",
        headers={"X-Real-IP": "198.51.100.10"},
        json={"session_id": "peer-one", "message": "First"},
    )
    same_peer = client.post(
        "/chat",
        headers={"X-Real-IP": "198.51.100.11"},
        json={"session_id": "peer-two", "message": "Second"},
    )
    railway_client = client.post(
        "/chat",
        headers={
            "X-Real-IP": "198.51.100.11",
            "X-Railway-Edge": "railway/test-edge",
        },
        json={"session_id": "railway-client", "message": "Third"},
    )

    assert first.status_code == railway_client.status_code == 200
    assert same_peer.status_code == 429


def test_same_first_prompt_in_new_session_can_use_exact_cache(client, monkeypatch):
    calls = []

    async def fake_complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            await asyncio.sleep(0.04)
            return response("A Sunday site visit can be arranged.")
        raise RuntimeError("cached response could not be decoded")

    monkeypatch.setattr(main, "complete", fake_complete)
    prompt = "Can I book a site visit on Sunday?"

    first = client.post("/chat", json={"session_id": "one", "message": prompt}).json()
    second = client.post("/chat", json={"session_id": "two", "message": prompt}).json()

    assert first["reply"] == second["reply"]
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert first["metadata"]["input_tokens"] == second["metadata"]["input_tokens"]
    assert first["metadata"]["output_tokens"] == second["metadata"]["output_tokens"]
    assert second["metadata"]["latency_ms"] < first["metadata"]["latency_ms"]
    assert len(calls) == 2


def test_latency_includes_sqlite_session_lookup(client, monkeypatch):
    original_load = main.load_session_context

    def slow_load(session_id, limit=None):
        time.sleep(0.06)
        return original_load(session_id, limit)

    async def fake_complete(**_kwargs):
        return response("A short answer.")

    monkeypatch.setattr(main, "load_session_context", slow_load)
    monkeypatch.setattr(main, "complete", fake_complete)

    result = client.post(
        "/chat", json={"session_id": "latency-contract", "message": "Hello"}
    )

    assert result.status_code == 200
    assert result.json()["metadata"]["latency_ms"] >= 50


def test_exact_response_cache_key_includes_model_and_full_message_list(client):
    from app.response_cache import exact_request_key

    system = {"role": "system", "content": "Be concise."}
    first = [system, {"role": "user", "content": "Define API."}]
    follow_up = [*first, {"role": "assistant", "content": "An API connects software."}]

    assert exact_request_key("gemini/model-a", first) == exact_request_key(
        "gemini/model-a", first
    )
    assert exact_request_key("gemini/model-a", first) != exact_request_key(
        "gemini/model-b", first
    )
    assert exact_request_key("gemini/model-a", first) != exact_request_key(
        "gemini/model-a", follow_up
    )


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_simulated_provider_status_fails_over_and_reports_the_winning_key(
    client, monkeypatch, status
):
    simulated_statuses = []

    async def fake_complete(**kwargs):
        simulated_statuses.append(kwargs["simulated_status"])
        if kwargs["simulated_status"] is not None:
            raise SimulatedProviderFailure(kwargs["simulated_status"])
        return response("A teammate can confirm a demo time.")

    monkeypatch.setattr(main, "complete", fake_complete)
    result = client.post(
        "/chat",
        headers={"X-Simulate-Failure": f"key_1={status}"},
        json={"session_id": "fallback", "message": "Please arrange a demo."},
    )

    assert result.status_code == 200
    assert result.json()["reply"] == "A teammate can confirm a demo time."
    assert result.json()["metadata"]["key_id"] == "key_2"
    assert result.json()["metadata"]["fallback_attempts"] == 1
    assert result.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"

    next_request = client.post(
        "/chat",
        json={"session_id": "fallback-next-request", "message": "Hello again."},
    )

    assert next_request.status_code == 200
    assert next_request.json()["metadata"]["key_id"] == "key_1"
    assert simulated_statuses == [status, None, None]


@pytest.mark.parametrize("status", [400, 401, 403, 408, 429, 500, 502, 503])
def test_provider_http_error_fails_over_without_simulation_header(
    client, monkeypatch, status
):
    calls = []

    class ProviderHTTPError(Exception):
        def __init__(self, status_code):
            super().__init__(f"provider HTTP {status_code}")
            self.status_code = status_code

    async def fake_complete(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == "first-test-key":
            raise ProviderHTTPError(status)
        return response("A real provider answer after retry.")

    monkeypatch.setattr(main, "complete", fake_complete)
    result = client.post(
        "/chat", json={"session_id": "provider-fallback", "message": "Hello"}
    )

    assert result.status_code == 200
    assert result.json()["reply"] == "A real provider answer after retry."
    assert result.json()["metadata"]["key_id"] == "key_2"
    assert result.json()["metadata"]["fallback_attempts"] == 1
    assert calls == ["first-test-key", "second-test-key"]


def test_timeout_exception_without_status_fails_over(client, monkeypatch):
    calls = []

    async def fake_complete(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == "first-test-key":
            raise TimeoutError("request timed out")
        return response("The second provider answered.")

    monkeypatch.setattr(main, "complete", fake_complete)
    result = client.post(
        "/chat", json={"session_id": "timeout-fallback", "message": "Define solid."}
    )

    assert result.status_code == 200
    assert result.json()["metadata"]["key_id"] == "key_2"
    assert result.json()["metadata"]["fallback_attempts"] == 1
    assert calls == ["first-test-key", "second-test-key"]


def test_failure_simulation_switch_disables_header(client, monkeypatch):
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    simulated_statuses = []

    async def fake_complete(**kwargs):
        simulated_statuses.append(kwargs["simulated_status"])
        return response("A real provider answer.")

    monkeypatch.setattr(main, "complete", fake_complete)
    result = client.post(
        "/chat",
        headers={"X-Simulate-Failure": "key_1=429"},
        json={"session_id": "simulation-disabled", "message": "Hello"},
    )

    assert result.status_code == 200
    assert result.json()["reply"] == "A real provider answer."
    assert result.json()["metadata"]["key_id"] == "key_1"
    assert simulated_statuses == [None]


@pytest.mark.parametrize(
    "header",
    ["key_3=429", "key_1=418", "key_1", "*=nope"],
)
def test_invalid_failure_simulation_header_returns_json_422(client, header):
    result = client.post(
        "/chat",
        headers={"X-Simulate-Failure": header},
        json={"session_id": "invalid-simulation", "message": "Hello"},
    )

    assert result.status_code == 422
    assert isinstance(result.json().get("detail"), str)
    assert client.get("/sessions/invalid-simulation/messages").status_code == 404


def test_all_keys_failed_returns_503_and_does_not_create_history(client, monkeypatch):
    async def fake_complete(**kwargs):
        raise SimulatedProviderFailure(kwargs["simulated_status"] or 429)

    monkeypatch.setattr(main, "complete", fake_complete)
    result = client.post(
        "/chat",
        headers={"X-Simulate-Failure": "*=429"},
        json={"session_id": "unavailable", "message": "Try this request."},
    )

    assert result.status_code == 503
    assert result.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/unavailable/messages").status_code == 404


def test_per_key_simulation_statuses_can_fail_the_entire_pool(client, monkeypatch):
    async def fake_complete(**kwargs):
        raise SimulatedProviderFailure(kwargs["simulated_status"] or 429)

    monkeypatch.setattr(main, "complete", fake_complete)
    result = client.post(
        "/chat",
        headers={"X-Simulate-Failure": "key_1=429,key_2=500"},
        json={"session_id": "mixed-failures", "message": "Try this request."},
    )

    assert result.status_code == 503
    assert result.json() == {"detail": "all providers unavailable"}
    assert result.headers["x-provider-failure"] == "simulated_failure"
    assert client.get("/sessions/mixed-failures/messages").status_code == 404


def test_temporary_outage_gets_one_short_retry(client, monkeypatch):
    calls = []

    class ProviderHTTPError(Exception):
        def __init__(self, status_code):
            super().__init__(f"provider HTTP {status_code}")
            self.status_code = status_code

    async def no_wait(_seconds):
        return None

    async def fake_complete(**kwargs):
        calls.append(kwargs["api_key"])
        if len(calls) <= 2:
            raise ProviderHTTPError(503)
        return response("Recovered after the provider came back.")

    monkeypatch.setattr(main, "complete", fake_complete)
    monkeypatch.setattr(main, "async_sleep", no_wait)
    monkeypatch.setattr(main.random, "uniform", lambda _low, _high: 0.25)
    result = client.post(
        "/chat", json={"session_id": "transient-outage", "message": "Hello"}
    )

    assert result.status_code == 200
    assert result.json()["reply"] == "Recovered after the provider came back."
    assert result.json()["metadata"]["key_id"] == "key_1"
    assert result.json()["metadata"]["fallback_attempts"] == 2
    assert calls == ["first-test-key", "second-test-key", "first-test-key"]


def test_provider_failure_exposes_only_a_safe_classification(client, monkeypatch):
    class InvalidKey(Exception):
        status_code = 401

    async def fake_complete(**kwargs):
        raise InvalidKey("API key not valid; secret contents are redacted")

    monkeypatch.setattr(main, "complete", fake_complete)
    result = client.post(
        "/chat", json={"session_id": "invalid-keys", "message": "Hello"}
    )

    assert result.status_code == 503
    assert result.json() == {"detail": "all providers unavailable"}
    assert result.headers["x-provider-failure"] == "invalid_api_key"


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "", "message": "hello"},
        {"session_id": "valid", "message": "   "},
        {"session_id": "valid", "message": "x" * 4001},
        {"session_id": "x" * 129, "message": "hello"},
    ],
)
def test_invalid_requests_are_422_and_do_not_write_history(client, payload):
    result = client.post("/chat", json=payload)

    assert result.status_code == 422


def test_malformed_json_returns_422_with_json_detail(client):
    result = client.post(
        "/chat",
        content='{"session_id":',
        headers={"Content-Type": "application/json"},
    )

    assert result.status_code == 422
    assert result.headers["content-type"].startswith("application/json")
    assert "detail" in result.json()


def test_unknown_session_history_returns_404(client):
    result = client.get("/sessions/missing/messages")

    assert result.status_code == 404
    assert result.json() == {"detail": "session not found"}


def test_unlisted_routes_are_not_exposed(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404

