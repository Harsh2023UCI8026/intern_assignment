# Lyftr AI Conversation Workspace

An original full-stack implementation of the Lyftr AI LLM chatbot assignment. The interface takes visual cues from Lyftr AI's public site while using an original operations-console layout for this assignment. It pairs a multilingual SMB conversation with visible cache, fallback, token, latency, SQLite, and failure-simulation details.

This project is configured for free-tier use only: Docker locally, Gemini API free-tier keys, and an OCI Always Free VM for public deployment. Do not enable paid API billing or create paid cloud resources.

## 1. Live URLs

| Service | URL |
| --- | --- |
| Local frontend | http://localhost:3000 |
| Local backend | http://localhost:8000 |
| Public frontend | Add after OCI Always Free deployment |
| Public backend | Add after OCI Always Free deployment |

The assignment requires public frontend and backend URLs. They are not published from this workspace yet; OCI account setup and pushing this folder to a public GitHub repository are still required.

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

3. Save `.env` and build/start both services:

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

   To stop the app, run `docker compose down`. The SQLite file is stored at `/data/app.db` in the named `app_data` Docker volume and survives container rebuilds. The cache is process-local, so it is intentionally started with one backend worker.

### Free-tier API setup

The default model is `gemini/gemini-3.6-flash`, which Google currently lists with free-tier access. Free-tier model access and quotas can change or vary by project; check the [current pricing](https://ai.google.dev/gemini-api/docs/pricing) and [active rate limits](https://ai.google.dev/gemini-api/docs/rate-limits). The two keys satisfy the assignment's fallback contract; separate keys in one Google project do not multiply that project's quota. If a free-tier request is rate-limited, wait and retry rather than enabling paid billing.

### Free public deployment on OCI Always Free

OCI Always Free provides an Ampere A1 allowance equivalent to 2 OCPUs and 12 GB RAM, and 200 GB of block-volume storage in the account's home region. The free account also includes outbound transfer within its published allowance. Always Free capacity can be unavailable, and Oracle may reclaim an Always Free compute instance if its CPU, network, and (for A1) memory utilization all remain below the published thresholds for seven days. See [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [free-tier account terms](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm), and [OCI free services](https://www.oracle.com/cloud/free/).

1. Create an OCI Free Tier account. Choose your home region carefully. Most sign-ups need a phone number and payment card for verification; Oracle says the card is not charged unless you upgrade the account. Do not upgrade. In the cloud console, only create resources labeled **Always Free Eligible**; do not use the temporary trial credits for this deployment.
2. In the home region, create a VM.Standard.A1.Flex instance with Ubuntu, 1 OCPU, 6 GB RAM, and a public IPv4 address. Use an Always Free-eligible image. If A1 capacity is unavailable, wait or try another availability domain in the same home region; do not switch to a paid shape.
3. In the VM's security list or network security group, allow inbound TCP `3000` and `8000` from the internet so both assignment tiers are reachable. Restrict SSH TCP `22` to your own IP. Public URLs for this demo use HTTP and the VM's IP; a domain is not required.
4. Push this project to a **public** GitHub repository. Git is installed on the Windows machine. From the project folder, create an empty repository on GitHub, then run the following once, replacing the URL with your repository URL:

   ```powershell
   git init
   git add .
   git commit -m "Build resilient Lyftr AI chatbot assignment"
   git branch -M main
   git remote add origin https://github.com/YOUR_GITHUB_NAME/lyftr-ai-assignment.git
   git push -u origin main
   ```

   `.env` is ignored by `.gitignore`; check that no real keys are staged before pushing.
5. SSH to the VM using the private key you selected during instance creation, then install Docker Engine and the Compose plugin using [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/). Clone your public GitHub repository onto the VM.
6. On the VM, create `.env` from `.env.example`. Set `LLM_API_KEYS` to your two free-tier Gemini keys, and update these two origins to the VM's public IP:

   ```dotenv
   ALLOWED_ORIGINS=http://YOUR_PUBLIC_IP:3000
   VITE_API_BASE_URL=http://YOUR_PUBLIC_IP:8000
   ```

   Do not commit or share this `.env` file. The API keys stay in the backend environment.
7. In the repository directory on the VM, run `docker compose up -d --build`. Visit `http://YOUR_PUBLIC_IP:3000` and verify `http://YOUR_PUBLIC_IP:8000/health` returns `status: ok`. SQLite stays in the named `app_data` volume on the VM's boot disk across restarts and image rebuilds. Keep the VM and boot volume while the submission is live.

This deployment uses no paid Render service, paid disk, paid domain, or paid API plan. OCI capacity and Gemini's free quota are not guaranteed; never choose paid resources if the free allowance is unavailable.

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
- The service retries the next key for simulated failures, HTTP 429/rate-limit/quota errors, and provider 5xx responses. `key_id` is a safe label such as `key_2`; the secret value is never returned or logged.
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

The suite covers health readiness, request validation, multi-turn context, session isolation, oldest-first history, exact cache hits across sessions, fallback from `key_1` to `key_2`, all-keys-failed behavior, and unknown sessions.

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
- Customer scenario starters for real estate, clinic scheduling, and order support, including Hindi/Hinglish examples.
- One-click cache replay, key failover, and all-keys-down flows using the assignment's API contract.
- Per-reply source/model/latency/token telemetry, fallback metadata, and readable error states.
- A health overview for key count, cache, SQLite, and failure-simulation state.
- A custom system-prompt control saved per session after its first successful reply.
