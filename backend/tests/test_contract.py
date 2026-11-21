from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.llm import SimulatedProviderFailure


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
    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-3.6-flash")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("DEFAULT_SYSTEM_PROMPT", "You are a small business assistant.")
    monkeypatch.setattr(main, "initialize_litellm", lambda: True)
    monkeypatch.setattr(main, "cache_is_ready", lambda: True)
    with TestClient(main.app) as test_client:
        yield test_client


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


def test_same_first_prompt_in_new_session_can_use_exact_cache(client, monkeypatch):
    seen_messages = []

    async def fake_complete(**kwargs):
        key = repr((kwargs["model"], kwargs["messages"]))
        seen_messages.append(key)
        cached = len([value for value in seen_messages if value == key]) > 1
        return response("A Sunday site visit can be arranged.", cache_hit=cached)

    monkeypatch.setattr(main, "complete", fake_complete)
    prompt = "Can I book a site visit on Sunday?"

    first = client.post("/chat", json={"session_id": "one", "message": prompt}).json()
    second = client.post("/chat", json={"session_id": "two", "message": prompt}).json()

    assert first["reply"] == second["reply"]
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0


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
    assert simulated_statuses == [status, None]


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
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
    assert client.get("/sessions/mixed-failures/messages").status_code == 404


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


def test_unknown_session_history_returns_404(client):
    result = client.get("/sessions/missing/messages")

    assert result.status_code == 404
    assert result.json() == {"detail": "session not found"}


def test_unlisted_routes_are_not_exposed(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404

