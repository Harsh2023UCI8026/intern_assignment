from __future__ import annotations

import json
import logging
import random
import time
import uuid
from asyncio import sleep as async_sleep
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import ensure_english_system_prompt, get_settings
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
    provider_status_code,
    response_text,
    token_usage,
)
from .response_cache import CachedResponse, exact_request_key, response_cache

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
    expose_headers=["X-Request-ID", "X-Provider-Failure"],
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


def _provider_failure_kind(error: Exception) -> str:
    if isinstance(error, SimulatedProviderFailure):
        return "simulated_failure"
    status = provider_status_code(error)
    message = str(error).casefold()
    error_name = type(error).__name__.casefold()
    if status in {401, 403} or any(
        phrase in message
        for phrase in ("invalid api key", "api key not valid", "api_key_invalid")
    ):
        return "invalid_api_key"
    if status == 404 or any(
        phrase in message
        for phrase in ("model not found", "model_not_found", "unsupported model")
    ):
        return "model_unavailable"
    if status == 429 or any(
        phrase in message for phrase in ("quota", "resource_exhausted", "rate limit")
    ):
        return "quota_or_rate_limit"
    if (
        status == 408
        or isinstance(error, TimeoutError)
        or "timeout" in error_name
        or "timed out" in message
    ):
        return "provider_timeout"
    if status is not None and 500 <= status <= 599:
        return "provider_unavailable"
    return "provider_error"


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
    started = time.perf_counter()
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
    selected_prompt = (
        payload.system_prompt
        if prompt_was_overridden
        else (saved_prompt if session_exists and saved_prompt else settings.default_system_prompt)
    )
    system_prompt = ensure_english_system_prompt(selected_prompt)
    prompt_was_overridden = prompt_was_overridden or system_prompt != selected_prompt
    messages = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": payload.message},
    ]

    cache_key = exact_request_key(settings.model, messages)
    fallback_attempts = 0
    failure_kinds: list[str] = []
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
            cached_response = (
                response_cache.get(cache_key) if not simulated_failures else None
            )
            if cached_response is not None:
                answer = cached_response.reply
                input_tokens = cached_response.input_tokens
                output_tokens = cached_response.output_tokens
                metadata_source = "LLM_CACHE"
                winning_key_id = None
                fallback_attempts = 0
                break

            failure_kind = _provider_failure_kind(error)
            failure_kinds.append(failure_kind)
            logger.warning(
                json.dumps(
                    {
                        "event": "llm_attempt_failed",
                        "key_id": key_id,
                        "status": provider_status_code(error),
                        "classification": failure_kind,
                    },
                    separators=(",", ":"),
                )
            )
            # Keys can have different permissions or project quotas. Try the
            # remaining pool even for auth/model errors, without logging details.
            if index + 1 < len(settings.api_keys):
                fallback_attempts += 1
                continue
            break

    if answer is None:
        if not simulated_failures and any(
            kind in {"provider_timeout", "provider_unavailable"}
            for kind in failure_kinds
        ):
            await async_sleep(random.uniform(0.2, 0.45))
            retry_key = settings.api_keys[0]
            try:
                result = await complete(
                    model=settings.model,
                    api_key=retry_key,
                    messages=messages,
                    caching=True,
                    timeout_seconds=min(5.0, settings.request_timeout_seconds),
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
                    winning_key_id = "key_1"
                    fallback_attempts += 1
            except Exception as error:
                failure_kind = _provider_failure_kind(error)
                failure_kinds.append(failure_kind)
                logger.warning(
                    json.dumps(
                        {
                            "event": "llm_transient_retry_failed",
                            "key_id": "key_1",
                            "status": provider_status_code(error),
                            "classification": failure_kind,
                        },
                        separators=(",", ":"),
                    )
                )

        if answer is None:
            failure_kind = failure_kinds[-1] if failure_kinds else "provider_error"
            raise HTTPException(
                status_code=503,
                detail="all providers unavailable",
                headers={"X-Provider-Failure": failure_kind},
            )

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
    if not simulated_failures:
        response_cache.put(
            cache_key,
            CachedResponse(
                reply=answer,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )
    return {"session_id": payload.session_id, "reply": answer, "metadata": metadata}


@app.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str):
    messages = list_session_messages(session_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"session_id": session_id, "messages": messages}

