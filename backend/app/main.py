from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import get_settings
from .database import (
    database_is_ready,
    initialize_database,
    list_session_messages,
    load_session_context,
    save_successful_turn,
)
from .llm import (
    SimulatedProviderFailure,
    cache_is_ready,
    complete,
    initialize_litellm,
    is_cache_hit,
    is_retryable_provider_error,
    provider_status_code,
    response_text,
    token_usage,
)

logger = logging.getLogger("lyftr.api")
logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    initialize_litellm()
    yield


app = FastAPI(
    title="Lyftr AI Conversation Service",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def current_settings():
    return get_settings()


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(current_settings().allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Simulate-Failure"],
)


@app.middleware("http")
async def request_observability(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                },
                separators=(",", ":"),
            )
        )


@app.exception_handler(Exception)
async def safe_server_error(_: Request, exc: Exception):
    logger.error(
        json.dumps(
            {"event": "unhandled_error", "error_type": type(exc).__name__},
            separators=(",", ":"),
        )
    )
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: str | None = Field(default=None, max_length=4000)

    @field_validator("session_id", "message", mode="before")
    @classmethod
    def trim_required_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("session_id", "message")
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("system_prompt", mode="before")
    @classmethod
    def trim_optional_prompt(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip() or None
        return value


def _parse_simulation_header(
    value: str | None, enabled: bool, key_count: int
) -> dict[str, int]:
    if not value or not enabled:
        return {}
    failures: dict[str, int] = {}
    allowed_key_ids = {"*", *(f"key_{index}" for index in range(1, key_count + 1))}
    for pair in value.split(","):
        key_id, separator, raw_status = pair.strip().partition("=")
        if not separator or key_id not in allowed_key_ids:
            raise HTTPException(status_code=422, detail="invalid failure simulation header")
        try:
            status = int(raw_status)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="invalid failure simulation status"
            ) from exc
        if status not in {429, 500, 502, 503}:
            raise HTTPException(status_code=422, detail="unsupported simulation status")
        failures[key_id] = status
    return failures


def _text_for_error(error: Exception) -> str:
    status = provider_status_code(error)
    if (
        status == 408
        or isinstance(error, TimeoutError)
        or "timeout" in type(error).__name__.lower()
    ):
        return "provider request timed out"
    if status == 429 or "quota" in str(error).lower():
        return "provider quota or rate limit reached"
    if status is not None and status >= 500:
        return f"provider returned HTTP {status}"
    return type(error).__name__


@app.get("/health")
async def health(response: Response):
    settings = current_settings()
    db_ready = database_is_ready()
    cache_ready = cache_is_ready()
    ready = db_ready and cache_ready and len(settings.api_keys) >= 2
    if not ready:
        response.status_code = 503
    return {
        "status": "ok" if ready else "degraded",
        "keys_configured": len(settings.api_keys),
        "cache_enabled": cache_ready,
        "database_ready": db_ready,
        "failure_simulation_enabled": settings.failure_simulation_enabled,
    }


@app.post("/chat")
async def chat(
    payload: ChatRequest,
    x_simulate_failure: str | None = Header(default=None, alias="X-Simulate-Failure"),
):
    settings = current_settings()
    if len(settings.api_keys) < 2:
        raise HTTPException(
            status_code=503, detail="at least two LLM_API_KEYS are required"
        )
    if not cache_is_ready():
        raise HTTPException(status_code=503, detail="LiteLLM cache is unavailable")

    simulated_failures = _parse_simulation_header(
        x_simulate_failure, settings.failure_simulation_enabled, len(settings.api_keys)
    )
    session_exists, saved_prompt, history = load_session_context(payload.session_id)
    prompt_was_overridden = payload.system_prompt is not None
    system_prompt = (
        payload.system_prompt
        if prompt_was_overridden
        else (saved_prompt if session_exists and saved_prompt else settings.default_system_prompt)
    )
    messages = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": payload.message},
    ]

    started = time.perf_counter()
    fallback_attempts = 0
    answer: str | None = None
    final_model = settings.model
    winning_key_id: str | None = None
    metadata_source = "LIVE_INFERENCE"
    input_tokens = 0
    output_tokens = 0

    for index, api_key in enumerate(settings.api_keys):
        key_id = f"key_{index + 1}"
        try:
            result = await complete(
                model=settings.model,
                api_key=api_key,
                messages=messages,
                caching=not bool(simulated_failures),
                timeout_seconds=settings.request_timeout_seconds,
                simulated_status=simulated_failures.get(key_id)
                or simulated_failures.get("*"),
            )
            answer = response_text(result)
            if not answer:
                raise RuntimeError("provider returned an empty response")
            input_tokens, output_tokens = token_usage(result)
            if is_cache_hit(result):
                metadata_source = "LLM_CACHE"
                winning_key_id = None
                fallback_attempts = 0
            else:
                winning_key_id = key_id
            break
        except Exception as error:
            if index + 1 < len(settings.api_keys) and (
                isinstance(error, SimulatedProviderFailure)
                or is_retryable_provider_error(error)
            ):
                fallback_attempts += 1
                continue
            logger.warning(
                json.dumps(
                    {
                        "event": "llm_attempt_failed",
                        "key_id": key_id,
                        "status": provider_status_code(error),
                        "error": _text_for_error(error),
                    },
                    separators=(",", ":"),
                )
            )
            break

    if answer is None:
        raise HTTPException(status_code=503, detail="all providers unavailable")

    latency_ms = int((time.perf_counter() - started) * 1000)
    metadata = {
        "resolution_source": metadata_source,
        "model": final_model,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "key_id": winning_key_id,
        "fallback_attempts": fallback_attempts,
    }
    save_successful_turn(
        session_id=payload.session_id,
        user_message=payload.message,
        assistant_reply=answer,
        metadata=metadata,
        system_prompt=system_prompt,
        prompt_was_overridden=prompt_was_overridden,
    )
    return {"session_id": payload.session_id, "reply": answer, "metadata": metadata}


@app.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str):
    messages = list_session_messages(session_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "messages": messages}

