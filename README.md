# Lyftr AI Conversation Workspace

An original full-stack implementation of the Lyftr AI LLM chatbot assignment. The interface takes visual cues from Lyftr AI's public site while using an original operations-console layout for this assignment. It pairs a multilingual SMB conversation with visible cache, fallback, token, latency, SQLite, and failure-simulation details.

This project is configured for free-tier use only: Docker locally, Gemini API free-tier keys, and Railway's Free plan for public deployment. Railway services sleep while idle and stop when their monthly free usage credit is exhausted. Do not choose a paid plan or enable paid API billing.

## 1. Live URLs

| Service | URL |
| --- | --- |
| Local frontend | http://localhost:3000 |
| Local backend | http://localhost:8000 |
| Public frontend | Add after Railway deployment |
| Public backend | Add after Railway deployment |

The assignment requires public frontend and backend URLs. They are not published from this workspace yet; a Railway account must be connected to the GitHub repository and the service variables below must be set.

## 2. Run locally

### With Docker on Windows

Docker Desktop and its Linux engine must be running. Node.js and Python do not need to be installed on Windows.

1. Open this project folder in PowerShell. A private `.env` file is already prepared and ignored by Git. If you make a fresh clone, create it with:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Open `.env` and replace the two sample values on this one line with two real, free-tier Gemini API keys:

   ```dotenv
   LLM_API_KEYS=PASTE_FIRST_GEMINI_KEY_HERE,PASTE_SECOND_GEMINI_KEY_HERE
   ```

   Create keys at [Google AI Studio API keys](https://aistudio.google.com/apikey). Stay on the free tier and do not enable billing. Copy each key exactly; keep the comma between them, and do not add quotes or spaces. Never put keys in `frontend/src`, GitHub, or this chat.

3. Save `.env` and build/start both services with the required Makefile target:

   ```powershell
   make up
   ```

   If GNU Make is unavailable in PowerShell, run the equivalent Docker Compose command:

   ```powershell
   docker compose up -d --build
   ```

4. Confirm readiness:

   ```powershell
   Invoke-RestMethod http://localhost:8000/health
   ```

   With two non-placeholder keys, a reachable SQLite database, and an initialized cache, this returns HTTP 200 with `status: ok`. If the API-key line still contains `PASTE_...`, health deliberately stays `503 degraded` and reports zero configured keys. The service does not claim an AI provider is ready before the required key pool is present. Check backend startup details with `docker compose logs --tail=100 backend`.

5. Open the app at [http://localhost:3000](http://localhost:3000). The API is at [http://localhost:8000](http://localhost:8000). After changing `.env`, recreate the backend so Docker loads the new values:

   ```powershell
   docker compose up -d --force-recreate backend
   ```

   To stop the app, run `make down` (or `docker compose down` if GNU Make is unavailable). The SQLite file is stored at `/data/app.db` in the named `app_data` Docker volume and survives container rebuilds. The cache is process-local, so it is intentionally started with one backend worker.

   The host ports are configurable with `FRONTEND_PORT` and `BACKEND_PORT` in `.env`; defaults are `3000` and `8000`. If you change either port, keep `VITE_API_BASE_URL` and `ALLOWED_ORIGINS` aligned with the browser-visible URLs.

### Free-tier API setup

The default model is `gemini/gemini-3.6-flash`, which Google currently lists with free-tier access. Free-tier model access and quotas can change or vary by project; check the [current pricing](https://ai.google.dev/gemini-api/docs/pricing) and [active rate limits](https://ai.google.dev/gemini-api/docs/rate-limits). The two keys satisfy the assignment's fallback contract; separate keys in one Google project do not multiply that project's quota. If a free-tier request is rate-limited, wait and retry rather than enabling paid billing.

### Free public deployment on Railway

Railway's Free plan is $0/month, requires no card, includes $1/month in usage credit, one persistent 0.5 GB volume, and a generated public domain per service. Services sleep after roughly 5–10 minutes without outbound traffic and wake on the next request; they stop when the monthly credit is exhausted. For new accounts, switch from the one-time Trial to Free after the trial ends and redeploy. Railway documents that Free/Trial volume data can be deleted 30 days after plan expiry, so export important demo history. See [Free plan](https://docs.railway.com/pricing/free-plan), [volumes](https://docs.railway.com/volumes/reference), and [pricing plans/data retention](https://docs.railway.com/pricing/plans).

Create two services from `Harsh2023UCI8026/intern_assignment`, both tracking `main`:

1. Create a Railway project and add a service from the GitHub repository. In its settings, set **Root Directory** to `/backend`; Railway will use `backend/Dockerfile`.
2. Add a volume to the backend service mounted at `/data`. Set its variables:

   ```dotenv
   DATABASE_URL=sqlite:////data/app.db
   LLM_API_KEYS=<two free Gemini keys separated by a comma>
   LLM_MODEL=gemini/gemini-3.6-flash
   ENABLE_FAILURE_SIMULATION=true
   LLM_TIMEOUT_SECONDS=20
   DEFAULT_SYSTEM_PROMPT=You are a helpful assistant for an Indian small business. Reply in the customer's language.
   ```

   Add the secret keys directly in Railway's Variables UI. Never put them in GitHub or the frontend.
3. Deploy the backend, open **Settings → Networking**, generate its public domain, and confirm `https://<backend-domain>/health` returns `status: ok`.
4. Add another service from the same repository. Set **Root Directory** to `/frontend`. In its build variables, set `VITE_API_BASE_URL=https://<backend-domain>` and deploy it. Set the service's public target port to `3000` (the bundled Nginx image listens on that port), then generate its domain.
5. Update the backend's `ALLOWED_ORIGINS` to exactly `https://<frontend-domain>` and redeploy it. Open the frontend URL and verify chat, cache replay, failover, all-keys-down, and session-history endpoints.

Keep both services on the Free plan. The $1 usage allowance is a hard stop rather than unlimited hosting; do not upgrade if it runs out. Fly.io is intentionally not used: its current free trial is limited to 2 VM-hours or 7 days and apps stop until a payment method is added.

### Without Docker (optional)

Install Python 3.12 and Node.js 22, then install the backend dependencies and run each service in its own PowerShell window:

```powershell
cd backend
python -m venv .venv
\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

In another window:

```powershell
cd frontend
npm ci
npm run dev
```

Set the backend environment variables from `.env` before starting Uvicorn. For a non-Docker database path use `DATABASE_URL=sqlite:///./data/app.db`; add `http://localhost:5173` to `ALLOWED_ORIGINS` and set `VITE_API_BASE_URL=http://localhost:8000` for the Vite dev server.

## 3. Design decisions

### Key fallback

- `LLM_API_KEYS` is an ordered, comma-separated pool. The app uses LiteLLM with the selected `LLM_MODEL`; provider keys remain server-side.
- The service retries the next key for simulated failures, HTTP 408 timeouts, HTTP 429/rate-limit/quota errors, and provider 5xx responses. `key_id` is a safe label such as `key_2`; the secret value is never returned or logged.
- If every key fails, the API returns `503 {"detail":"all providers unavailable"}`. A failed turn is not added to history.
- `X-Simulate-Failure` is honored only when `ENABLE_FAILURE_SIMULATION=true`. Simulated requests bypass cache reads so the failover can be demonstrated with a real provider answer.

### Caching

- LiteLLM's built-in local cache uses the selected model and complete message list: system prompt, prior turns, and the new user message. Session ID and API key are excluded, so an identical first prompt in a new session can hit cache.
- Cache responses retain the original reply and per-request token usage, report `LLM_CACHE`, set `key_id` to null, and reset `fallback_attempts` to zero.
- The local cache is process-local. The backend intentionally uses one worker; no paid or external cache service is required.

### SQLite session storage

- SQLite is the only database. `messages` stores the assignment's required fields; `sessions` stores the optional per-session system prompt.
- A complete user/assistant turn is written in one transaction only after a successful model response. Failed requests never enter history.
- `GET /sessions/{session_id}/messages` returns rows oldest first and omits the system prompt. A session without a successful turn returns 404.
- SQLite uses WAL mode, a session index, foreign-key enforcement, and the Docker named volume mounted at `/data`.

### API and security

The service exposes exactly the required application routes: `GET /health`, `POST /chat`, and `GET /sessions/{session_id}/messages`. FastAPI docs and OpenAPI routes are disabled. CORS uses `ALLOWED_ORIGINS`; request logs contain request metadata but never chat text or API keys. The `.env` file is ignored by Git.

## 4. Verification

The backend tests use a mocked provider and make no paid API calls. Run them in Docker with `docker compose run --rm backend pytest -q` or `make test` when GNU Make is available.

The suite covers health readiness, frontend CORS preflight, request validation, multi-turn context, session isolation, oldest-first history, exact cache hits across sessions, simulated 429/500/502/503 fallback, actual provider-status fallback including 408/timeouts, disabled and malformed failure hooks, per-key all-provider failure, metadata fields, and unknown sessions.

For a live smoke check, use `Invoke-RestMethod http://localhost:8000/health`, send a chat request to `/chat`, then load `/sessions/{session_id}/messages`. The included UI demo buttons also exercise cache replay, key failover, and the clean all-keys-down 503 path.

### 2-3 minute demo outline

1. Show `/health` and the reliability panel.
2. Use the Hinglish property inquiry preset and a follow-up question to show multi-turn context.
3. Click **Replay a cached answer** twice; the second fresh session should show `LLM_CACHE` and an identical reply.
4. Click **Simulate key failover**; show `key_2` and one fallback attempt.
5. Click **Simulate all keys down**; show the readable 503 and verify the failed turn is absent from session history.

## 5. Setup Used

- Editor: Codex workspace
- AI tools: OpenAI Codex for research, implementation, and documentation
- Stack: React, TypeScript, Vite, Python, FastAPI, LiteLLM, SQLite, Docker Compose

## 6. Bonus features (if any)

- A responsive Lyftr-inspired operations console rather than a plain chat window.
- The supplied Lyftr AI logo, plus a viewport-fitted chat layout with independent scrolling inside the message history and expanded side panels.
- Independently collapsible Tools and Insights sidebars, with compact mobile controls and slide-in drawers.
- Customer scenario starters for real estate, clinic scheduling, and order support, including Hindi/Hinglish examples.
- One-click cache replay, key failover, and all-keys-down flows using the assignment's API contract.
- Per-reply source/model/latency/token telemetry, fallback metadata, and readable error states.
- A health overview for key count, cache, SQLite, and failure-simulation state.
- A custom system-prompt control saved per session after its first successful reply.
