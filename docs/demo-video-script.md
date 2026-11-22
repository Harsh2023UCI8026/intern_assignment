# 2–3 Minute Demo Video Script

Record the deployed app and terminal in one screen recording. Use your own voice, keep API keys and Railway Variables off-screen, and submit the recording URL in the assignment form.

## Before recording

- Open the [frontend](https://brilliant-tenderness-production-33de6e49.up.railway.app) and confirm its status says **Ready**.
- Keep the [backend health endpoint](https://internassignment-production.up.railway.app/health) available in a browser tab.
- The cache is process-local. If the first cache replay already reports `LLM_CACHE`, restart the backend service before recording so the first replay can demonstrate a live result.
- The failover button makes one real request using the second Gemini key. Stay within the configured free quota; never enable billing for the demo.

## Timed walkthrough and narration

### 0:00–0:20 — App and health

**Show:** Open the deployed frontend, then the backend `/health` endpoint.

**Say:** “This is my deployed Lyftr AI conversation app. The backend health endpoint confirms that the API-key pool, response cache, SQLite database, and failure simulation are ready.”

### 0:20–0:50 — Multi-turn chat and metadata

**Show:** In the frontend, send: `I am looking for a 3BHK in Jaipur. Can I visit this weekend?` Then ask: `Which city and property type did I mention?` Point to the assistant replies and their source, model, latency, and per-request token counts.

**Say:** “The assistant keeps context within this session. Each reply shows whether it came from live inference or cache, which model answered, the latency, and this request’s input and output tokens.”

### 0:50–1:15 — POST chat and GET session history

**Show:** In PowerShell, run the following. It makes one short live request, then reads the saved messages for that session.

```powershell
$api = "https://internassignment-production.up.railway.app"
$session = "video-demo-$([guid]::NewGuid().ToString('N'))"
$body = @{ session_id = $session; message = "Explain a response cache in one short sentence." } | ConvertTo-Json -Compress
Invoke-RestMethod -Uri "$api/chat" -Method Post -ContentType "application/json" -Body $body
Invoke-RestMethod -Uri "$api/sessions/$session/messages"
```

**Say:** “The POST endpoint returns the assistant reply and metadata. The session-history GET endpoint returns the successful user and assistant messages in order.”

### 1:15–1:40 — Cache hit

**Show:** Click **Replay a cached answer** once, then click it again. Point out the identical reply and the second response’s `LLM_CACHE` source and lower latency.

**Say:** “The first request generates a response. The same prompt in a fresh session is served from the exact-response cache, with the original per-request token usage.”

### 1:40–2:05 — Key failover

**Show:** Click **Simulate key failover**. Point out the real assistant reply, `key_2`, and one fallback attempt.

**Say:** “This request simulates key 1 being rate-limited. The backend automatically tries key 2 and returns a real provider answer.”

### 2:05–2:30 — All keys unavailable

**Show:** Click **Simulate all keys down**. Point out the readable error, then finish on the healthy app or `/health` page.

**Say:** “When every key fails, the API returns a clear 503 response. The failed turn is not added to SQLite history. The simulation is request-scoped and does not exhaust real provider keys.”

