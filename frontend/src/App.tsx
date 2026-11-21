import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Building2,
  Check,
  ChevronDown,
  CircleHelp,
  Clock3,
  Command,
  Database,
  Headset,
  HeartHandshake,
  KeyRound,
  LoaderCircle,
  MessageCircle,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  TriangleAlert,
  Wifi,
  Zap,
  type LucideIcon,
} from "lucide-react";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(
  /\/$/,
  "",
);

type ResolutionSource = "LIVE_INFERENCE" | "LLM_CACHE";
type ReplyMetadata = {
  resolution_source: ResolutionSource;
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  key_id: string | null;
  fallback_attempts: number;
};
type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "notice";
  content: string;
  time?: string;
  metadata?: ReplyMetadata;
  tone?: "error" | "success";
};
type Health = {
  status: string;
  keys_configured: number;
  cache_enabled: boolean;
  database_ready?: boolean;
  failure_simulation_enabled?: boolean;
};
type Scenario = { title: string; caption: string; message: string; icon: LucideIcon };

const scenarios: Scenario[] = [
  {
    title: "Property inquiry",
    caption: "Lead qualification · Hinglish",
    message:
      "Namaste, mujhe Sector 48 Gurgaon mein 3BHK dekhna hai. Budget 1.5 crore tak hai. Kya is weekend site visit mil sakti hai?",
    icon: Building2,
  },
  {
    title: "Clinic appointment",
    caption: "Scheduling · Hindi + English",
    message:
      "Hi, can I book a dentist appointment for Saturday morning? Please reply in simple Hindi.",
    icon: Stethoscope,
  },
  {
    title: "Order support",
    caption: "Customer support · E-commerce",
    message:
      "My order is late and I need it before Friday. Can you check the status and tell me what to do next?",
    icon: Headset,
  },
];

const CACHE_PROMPT =
  "A customer asks in Hinglish: 'Kya aap Sunday ko site visit book kar sakte ho?' Reply in one helpful sentence.";

function makeId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();

  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function createSessionId() {
  return `s-${makeId()}`;
}

function shortSession(id: string) {
  return id.slice(0, 14);
}

function formatTime() {
  return new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date());
}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") {
      if (body.detail === "all providers unavailable") {
        return "AI providers are temporarily unavailable. Please retry shortly; this turn was not saved.";
      }
      return body.detail;
    }
    if (Array.isArray(body.detail)) {
      const details = body.detail
        .map((issue: { loc?: unknown; msg?: unknown }) => {
          const location = Array.isArray(issue?.loc)
            ? issue.loc.filter((part) => part !== "body").join(".")
            : "";
          const message = typeof issue?.msg === "string" ? issue.msg : "Invalid input";
          return location ? `${location}: ${message}` : message;
        })
        .filter(Boolean);
      if (details.length) return details.join("; ");
    }
    return "The request could not be completed.";
  } catch {
    return "The service returned an unreadable response.";
  }
}

function healthStatusDetail(health: Health | null): string {
  if (!health) return "Backend is unreachable. Check Docker and the API URL.";
  if (!health.database_ready) return "SQLite is unavailable. Check the Docker data volume.";
  if (!health.cache_enabled) return "LiteLLM cache did not initialize. Check backend logs.";
  if (health.keys_configured < 2) {
    return "Paste 2 free Gemini keys on LLM_API_KEYS in .env, then recreate the backend.";
  }
  return "Backend is running but not ready. Check its logs for details.";
}

function App() {
  const [sessionId, setSessionId] = useState(createSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [promptOpen, setPromptOpen] = useState(false);
  const [leftSidebarExpanded, setLeftSidebarExpanded] = useState(false);
  const [rightSidebarExpanded, setRightSidebarExpanded] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState("");
  const conversationRef = useRef<HTMLDivElement>(null);
  const textAreaRef = useRef<HTMLTextAreaElement>(null);

  const refreshHealth = useCallback(async (showLoading = true) => {
    if (showLoading) setHealthLoading(true);
    try {
      const response = await fetch(`${API_BASE}/health`, { cache: "no-store" });
      const data = (await response.json()) as Health;
      setHealth(data);
    } catch {
      setHealth(null);
    } finally {
      if (showLoading) setHealthLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
    const healthPoll = window.setInterval(() => void refreshHealth(false), 10_000);
    return () => window.clearInterval(healthPoll);
  }, [refreshHealth]);

  useEffect(() => {
    conversationRef.current?.scrollTo({
      top: conversationRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, sending]);

  useEffect(() => {
    const textarea = textAreaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 136)}px`;
  }, [draft]);

  const startNewConversation = useCallback((keepPrompt = true) => {
    const nextSession = createSessionId();
    setSessionId(nextSession);
    setMessages([]);
    if (!keepPrompt) setSystemPrompt("");
    setNotice("A fresh session is ready. Earlier sessions remain stored in SQLite.");
    window.setTimeout(() => setNotice(""), 4000);
    return nextSession;
  }, []);

  const sendMessage = useCallback(
    async (
      text: string,
      options?: { sessionId?: string; simulatedFailure?: string },
    ) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;
      const targetSession = options?.sessionId || sessionId;
      setSending(true);
      setMessages((current) => [
        ...current,
        { id: makeId(), role: "user", content: trimmed, time: formatTime() },
      ]);
      setDraft("");
      if (textAreaRef.current) textAreaRef.current.style.height = "auto";

      try {
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (options?.simulatedFailure) {
          headers["X-Simulate-Failure"] = options.simulatedFailure;
        }
        const payload: Record<string, string> = {
          session_id: targetSession,
          message: trimmed,
        };
        if (systemPrompt.trim()) payload.system_prompt = systemPrompt.trim();

        const response = await fetch(`${API_BASE}/chat`, {
          method: "POST",
          headers,
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          const detail = await readError(response);
          setMessages((current) => [
            ...current,
            {
              id: makeId(),
              role: "notice",
              content: detail,
              time: formatTime(),
              tone: "error",
            },
          ]);
          return;
        }

        const data = (await response.json()) as {
          reply: string;
          metadata: ReplyMetadata;
        };
        setMessages((current) => [
          ...current,
          {
            id: makeId(),
            role: "assistant",
            content: data.reply,
            time: formatTime(),
            metadata: data.metadata,
          },
        ]);
        void refreshHealth(false);
      } catch {
        setMessages((current) => [
          ...current,
          {
            id: makeId(),
            role: "notice",
            content: "Could not reach the API. Check the backend URL and try again.",
            time: formatTime(),
            tone: "error",
          },
        ]);
      } finally {
        setSending(false);
      }
    },
    [refreshHealth, sending, sessionId, systemPrompt],
  );

  const runCacheReplay = useCallback(async () => {
    const nextSession = startNewConversation();
    await sendMessage(CACHE_PROMPT, { sessionId: nextSession });
  }, [sendMessage, startNewConversation]);

  const runFailover = useCallback(async () => {
    if (!health?.failure_simulation_enabled) {
      setNotice("Enable ENABLE_FAILURE_SIMULATION=true on the backend to run this demo.");
      window.setTimeout(() => setNotice(""), 5000);
      return;
    }
    const uniqueQuestion =
      "Namaste, I run a shop in Jaipur and would like to book a product demo this week. Can you help me with a time?";
    await sendMessage(uniqueQuestion, { simulatedFailure: "key_1=429" });
  }, [health?.failure_simulation_enabled, sendMessage]);

  const runAllKeysDown = useCallback(async () => {
    if (!health?.failure_simulation_enabled) {
      setNotice("Enable ENABLE_FAILURE_SIMULATION=true on the backend to run this demo.");
      window.setTimeout(() => setNotice(""), 5000);
      return;
    }
    const uniqueQuestion =
      "A customer from Jaipur is asking for a demo time this week. Please reply in one short sentence.";
    await sendMessage(uniqueQuestion, { simulatedFailure: "*=429" });
  }, [health?.failure_simulation_enabled, sendMessage]);

  const submit = useCallback(() => {
    void sendMessage(draft);
  }, [draft, sendMessage]);

  const healthIsReady = health?.status === "ok";
  const latestAssistantMessage = [...messages]
    .reverse()
    .find((item) => item.role === "assistant");
  const latestMetadata = latestAssistantMessage?.metadata;

  return (
    <div className="app-shell">
      <header className="topbar" id="home">
        <a className="brand" href="#home" aria-label="Lyftr AI home">
          <span className="brand-mark" aria-hidden="true">
            <span />
            <span />
          </span>
          <span className="brand-copy">
            <strong>Lyftr AI</strong>
            <small>Conversation workspace</small>
          </span>
        </a>

        <nav className="top-nav" aria-label="Workspace">
          <a className="nav-item active" href="#conversation">
            <MessageSquareText size={16} /> Conversations
          </a>
          <a className="nav-item" href="#reliability">
            <Activity size={16} /> Reliability
          </a>
        </nav>

        <div className="top-actions">
          <a className="help-link" href="https://lyftr.ai/about" target="_blank" rel="noreferrer">
            <CircleHelp size={16} /> About Lyftr
          </a>
          <button className="button button-primary button-small" onClick={() => startNewConversation()}>
            <Plus size={16} strokeWidth={2.4} /> New conversation
          </button>
        </div>
      </header>

      <main className="workspace">
        <section className="page-intro">
          <div className="intro-copy">
            <div className="eyebrow">
              <span className="eyebrow-dot" /> AI FRONT DESK <span className="eyebrow-divider">/</span> ASSIGNMENT BUILD
            </div>
            <h1>
              Every lead gets an <span>answer.</span>
            </h1>
            <p>
              A dependable conversation layer for Indian businesses—built for multilingual
              customer support, even when a provider is not.
            </p>
          </div>
          <div className={`connection-pill ${healthLoading ? "is-loading" : healthIsReady ? "is-ready" : "is-offline"}`}>
            <span className="connection-dot" />
            <span>{healthLoading ? "Checking system" : healthIsReady ? "System operational" : "Action needed"}</span>
            <button className="icon-button refresh-button" onClick={() => void refreshHealth()} aria-label="Refresh system status">
              <Activity size={15} />
            </button>
          </div>
        </section>

        {notice && (
          <div className="toast" role="status">
            <Check size={15} /> {notice}
            <button className="toast-close" onClick={() => setNotice("")} aria-label="Dismiss message">×</button>
          </div>
        )}

        <div className={`workspace-grid ${leftSidebarExpanded ? "left-expanded" : "left-collapsed"} ${rightSidebarExpanded ? "right-expanded" : "right-collapsed"}`}>
          <aside
            className={`left-column sidebar left-sidebar ${leftSidebarExpanded ? "is-expanded" : "is-collapsed"}`}
            aria-label="Conversation tools"
            onKeyDown={(event) => {
              if (event.key === "Escape") setLeftSidebarExpanded(false);
            }}
          >
            <button
              className="sidebar-scrim"
              type="button"
              tabIndex={leftSidebarExpanded ? 0 : -1}
              aria-label="Close conversation tools"
              onClick={() => setLeftSidebarExpanded(false)}
            />
            <div className="sidebar-rail">
              <button
                className="sidebar-rail-toggle"
                type="button"
                aria-label={leftSidebarExpanded ? "Collapse conversation tools" : "Expand conversation tools"}
                aria-controls="left-sidebar-content"
                aria-expanded={leftSidebarExpanded}
                title={leftSidebarExpanded ? "Collapse tools" : "Expand tools"}
                onClick={() => setLeftSidebarExpanded((expanded) => !expanded)}
              >
                <PanelLeftOpen size={19} />
                <span>Tools</span>
              </button>
              <div className="sidebar-rail-icons" aria-hidden="true">
                <Sparkles size={16} />
                <Zap size={16} />
              </div>
            </div>
            <div id="left-sidebar-content" className="sidebar-expanded-content" aria-hidden={!leftSidebarExpanded}>
              <div className="sidebar-expanded-heading">
                <div><small>WORKSPACE</small><strong>Quick tools</strong></div>
                <button className="sidebar-collapse-button" type="button" aria-label="Collapse conversation tools" onClick={() => setLeftSidebarExpanded(false)}>
                  <PanelLeftClose size={17} />
                </button>
              </div>
              <div className="sidebar-panels">
            <div className="panel panel-playbook">
              <div className="panel-heading">
                <div>
                  <div className="panel-kicker">GET STARTED</div>
                  <h2>Try a scenario</h2>
                </div>
                <span className="heading-icon"><Sparkles size={17} /></span>
              </div>
              <p className="panel-description">Choose a real customer moment to start the conversation.</p>
              <div className="scenario-list">
                {scenarios.map((scenario) => {
                  const ScenarioIcon = scenario.icon;
                  return (
                    <button
                      className="scenario-card"
                      key={scenario.title}
                      onClick={() => {
                        setDraft(scenario.message);
                        textAreaRef.current?.focus();
                      }}
                    >
                      <span className="scenario-icon"><ScenarioIcon size={17} /></span>
                      <span className="scenario-copy">
                        <strong>{scenario.title}</strong>
                        <small>{scenario.caption}</small>
                      </span>
                      <ArrowUpRight className="scenario-arrow" size={15} />
                    </button>
                  );
                })}
              </div>
              <button className="text-button scenario-link" onClick={() => setPromptOpen((open) => !open)}>
                <Settings2 size={15} /> Tune this assistant <ChevronDown className={promptOpen ? "chevron-open" : ""} size={15} />
              </button>
              {promptOpen && (
                <div className="prompt-editor">
                  <label htmlFor="system-prompt">System prompt <span>OPTIONAL</span></label>
                  <textarea
                    id="system-prompt"
                    data-testid="system-prompt-input"
                    value={systemPrompt}
                    onChange={(event) => setSystemPrompt(event.target.value)}
                    placeholder="Set the assistant’s role and tone for this session…"
                    maxLength={4000}
                    rows={4}
                  />
                  <small>Saved with this session after the first successful reply.</small>
                </div>
              )}
            </div>

            <div className="panel panel-demo">
              <div className="panel-heading compact-heading">
                <div>
                  <div className="panel-kicker">ENGINEERING DEMO</div>
                  <h2>Prove resilience</h2>
                </div>
                <span className="demo-spark"><Zap size={15} fill="currentColor" /></span>
              </div>
              <p className="panel-description">Show how the assistant handles repeat prompts and provider failures.</p>
              <button className="demo-action" onClick={() => void runCacheReplay()} disabled={sending}>
                <span className="demo-action-icon cache-icon"><Database size={16} /></span>
                <span><strong>Replay a cached answer</strong><small>Fresh session · same prompt</small></span>
                <ArrowRight size={15} />
              </button>
              <button className="demo-action" onClick={() => void runFailover()} disabled={sending || !health?.failure_simulation_enabled}>
                <span className="demo-action-icon failover-icon"><Zap size={16} /></span>
                <span><strong>Simulate key failover</strong><small>key_1 rate limited → key_2</small></span>
                <ArrowRight size={15} />
              </button>
              <button className="demo-action" onClick={() => void runAllKeysDown()} disabled={sending || !health?.failure_simulation_enabled}>
                <span className="demo-action-icon outage-icon"><TriangleAlert size={16} /></span>
                <span><strong>Simulate all keys down</strong><small>Safe 503 · no history saved</small></span>
                <ArrowRight size={15} />
              </button>
              {!health?.failure_simulation_enabled && !healthLoading && (
                <div className="demo-note"><CircleHelp size={14} /> Enable the failure hook in backend settings.</div>
              )}
            </div>

            <div className="privacy-note">
              <ShieldCheck size={16} /> <span>Keys stay server-side. They never reach the browser.</span>
            </div>
              </div>
            </div>
          </aside>

          <section className="conversation-column" id="conversation">
            <div className="conversation-card">
              <div className="conversation-header">
                <div className="conversation-title-wrap">
                  <div className="conversation-avatar"><MessageCircle size={18} /></div>
                  <div>
                    <div className="conversation-title-line">
                      <h2>Live conversation</h2>
                      <span className="whatsapp-tag"><span /> WhatsApp flow</span>
                    </div>
                    <p>Session <code>{shortSession(sessionId)}</code> <span className="meta-separator">·</span> New session on page load</p>
                  </div>
                </div>
                <button className="icon-button conversation-menu" aria-label="Start a new session" onClick={() => startNewConversation()}>
                  <Plus size={18} />
                </button>
              </div>

              <div
                className={`message-list ${messages.length === 0 ? "is-empty" : ""}`}
                ref={conversationRef}
                role="log"
                aria-live="polite"
                aria-relevant="additions text"
                aria-label="Conversation messages"
              >
                {messages.length === 0 ? (
                  <div className="empty-state">
                    <div className="empty-illustration">
                      <div className="empty-orbit orbit-one" />
                      <div className="empty-orbit orbit-two" />
                      <div className="empty-bubble"><Sparkles size={26} /></div>
                      <span className="orbit-dot dot-blue" />
                      <span className="orbit-dot dot-green" />
                    </div>
                    <div className="empty-kicker"><span /> LIVE AI ASSISTANT</div>
                    <h3>Ready when your next customer is.</h3>
                    <p>Start with a scenario on the left, or write a message below. The assistant keeps context for this session.</p>
                    <div className="suggestion-row">
                      <button onClick={() => setDraft("Can you help me book a product demo this week?")}>Book a demo <ArrowUpRight size={13} /></button>
                      <button onClick={() => setDraft("Mujhe apna budget bata kar sahi option chahiye.")}>Ask in Hinglish <ArrowUpRight size={13} /></button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="day-divider"><span /> TODAY <span /></div>
                    {messages.map((message) => (
                      <MessageRow key={message.id} message={message} />
                    ))}
                    {sending && (
                      <div className="typing-row" aria-live="polite">
                        <span className="assistant-avatar"><Sparkles size={14} /></span>
                        <div className="typing-bubble"><span /><span /><span /></div>
                        <small>Lyftr AI is thinking</small>
                      </div>
                    )}
                  </>
                )}
              </div>

              <div className="composer-wrap">
                <div className="composer">
                  <textarea
                    ref={textAreaRef}
                    data-testid="chat-input"
                    value={draft}
                    onChange={(event) => {
                      setDraft(event.target.value);
                      event.currentTarget.style.height = "auto";
                      event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 136)}px`;
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        submit();
                      }
                    }}
                    placeholder="Write a message in any language…"
                    rows={1}
                    maxLength={4000}
                    disabled={sending}
                    aria-label="Message"
                  />
                  <div className="composer-tools">
                    <div className="composer-hint"><span><Command size={12} /></span> Enter to send <i /> Shift + Enter for a new line</div>
                    <button
                      data-testid="send-button"
                      className="button button-primary send-button"
                      onClick={submit}
                      disabled={sending || !draft.trim()}
                      aria-label="Send message"
                    >
                      {sending ? <LoaderCircle size={16} className="spin" /> : <Send size={16} />}
                      <span>Send</span>
                    </button>
                  </div>
                </div>
                <div className="composer-footer">
                  <span><ShieldCheck size={13} /> Conversation context is stored privately in SQLite.</span>
                  <span>{draft.length}/4000</span>
                </div>
              </div>
            </div>
          </section>

          <aside
            className={`right-column sidebar right-sidebar ${rightSidebarExpanded ? "is-expanded" : "is-collapsed"}`}
            id="reliability"
            aria-label="Reliability insights"
            onKeyDown={(event) => {
              if (event.key === "Escape") setRightSidebarExpanded(false);
            }}
          >
            <button
              className="sidebar-scrim"
              type="button"
              tabIndex={rightSidebarExpanded ? 0 : -1}
              aria-label="Close reliability insights"
              onClick={() => setRightSidebarExpanded(false)}
            />
            <div className="sidebar-rail">
              <button
                className="sidebar-rail-toggle"
                type="button"
                aria-label={rightSidebarExpanded ? "Collapse reliability insights" : "Expand reliability insights"}
                aria-controls="right-sidebar-content"
                aria-expanded={rightSidebarExpanded}
                title={rightSidebarExpanded ? "Collapse insights" : "Expand insights"}
                onClick={() => setRightSidebarExpanded((expanded) => !expanded)}
              >
                <PanelRightOpen size={19} />
                <span>Insights</span>
              </button>
              <div className="sidebar-rail-icons" aria-hidden="true">
                <Activity size={16} />
                <Database size={16} />
              </div>
            </div>
            <div id="right-sidebar-content" className="sidebar-expanded-content" aria-hidden={!rightSidebarExpanded}>
              <div className="sidebar-expanded-heading">
                <div><small>OBSERVABILITY</small><strong>System insights</strong></div>
                <button className="sidebar-collapse-button" type="button" aria-label="Collapse reliability insights" onClick={() => setRightSidebarExpanded(false)}>
                  <PanelRightClose size={17} />
                </button>
              </div>
              <div className="sidebar-panels">
            <div className="panel health-panel">
              <div className="panel-heading">
                <div>
                  <div className="panel-kicker">SYSTEM STATUS</div>
                  <h2>Reliability overview</h2>
                </div>
                <button className="icon-button small-refresh" onClick={() => void refreshHealth()} aria-label="Refresh health">
                  <Activity size={15} />
                </button>
              </div>
              <div className={`health-banner ${healthLoading ? "loading" : healthIsReady ? "healthy" : "unhealthy"}`}>
                <span className="health-indicator"><span /></span>
                <div><strong>{healthLoading ? "Connecting to service" : healthIsReady ? "All systems ready" : "Setup required"}</strong>
                  <small>{healthLoading ? "Checking /health" : healthIsReady ? "API, cache and storage available" : healthStatusDetail(health)}</small>
                </div>
                <Wifi size={17} />
              </div>

              <div className="health-list">
                <HealthRow icon={KeyRound} label="Provider key pool" value={health ? `${health.keys_configured} keys` : "Not connected"} okay={Boolean(health && health.keys_configured >= 2)} />
                <HealthRow icon={Zap} label="Response cache" value={health?.cache_enabled ? "Enabled" : "Unavailable"} okay={Boolean(health?.cache_enabled)} />
                <HealthRow icon={Database} label="Session storage" value={health?.database_ready ? "SQLite ready" : "Waiting"} okay={Boolean(health?.database_ready)} />
              </div>

              <div className="health-footnote">
                <span className="pulse-dot" /> Health checks confirm storage, cache, and configured key count; live key validity is checked on request.
              </div>
            </div>

            <div className="panel response-panel">
              <div className="panel-heading compact-heading">
                <div>
                  <div className="panel-kicker">PER-REPLY TELEMETRY</div>
                  <h2>Built to stay fast</h2>
                </div>
                <span className="telemetry-icon"><Activity size={17} /></span>
              </div>
              <p className="panel-description">Every answer carries its own trace, so your team can see exactly what happened.</p>
              <div className="telemetry-preview">
                <div className="telemetry-preview-top"><span className="telemetry-status-dot" /> LAST RESPONSE <span className="preview-live">LIVE</span></div>
                <div className="telemetry-big"><span>{latestMetadata?.latency_ms ?? "—"}</span><small>ms</small><Clock3 size={16} /></div>
                <div className="telemetry-mini-grid">
                  <div><span>Source</span><strong>{latestMetadata ? latestMetadata.resolution_source === "LLM_CACHE" ? "Cached" : "Live" : "—"}</strong></div>
                  <div><span>Tokens</span><strong>{latestMetadata ? `${latestMetadata.input_tokens} in · ${latestMetadata.output_tokens} out` : "—"}</strong></div>
                </div>
              </div>
              <div className="response-benefits">
                <div><span className="benefit-icon"><Check size={13} /></span> Per-request token counts</div>
                <div><span className="benefit-icon"><Check size={13} /></span> Safe provider fallback</div>
                <div><span className="benefit-icon"><Check size={13} /></span> Exact-match response cache</div>
              </div>
            </div>

            <div className="support-card">
              <div className="support-art"><HeartHandshake size={21} /><span /><span /></div>
              <div><strong>AI speed. Human care.</strong><p>Built around the way Indian teams serve customers.</p></div>
              <a href="https://lyftr.ai/about" target="_blank" rel="noreferrer" aria-label="Learn about Lyftr AI"><ArrowUpRight size={16} /></a>
            </div>
            <div className="footer-meta"><span><BookOpen size={13} /> Assignment workspace</span><span>v1.0</span></div>
              </div>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}

function HealthRow({
  icon: Icon,
  label,
  value,
  okay,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  okay: boolean;
}) {
  return (
    <div className="health-row">
      <span className="health-row-icon"><Icon size={16} /></span>
      <span className="health-row-label">{label}</span>
      <span className={`health-row-value ${okay ? "ok" : "waiting"}`}><i />{value}</span>
    </div>
  );
}

function MessageRow({ message }: { message: ChatMessage }) {
  if (message.role === "notice") {
    return (
      <div className={`notice-message ${message.tone || "error"}`} role="alert">
        <span className="notice-icon">{message.tone === "success" ? <Check size={15} /> : <TriangleAlert size={15} />}</span>
        <div><strong>{message.tone === "success" ? "Request completed" : "Could not complete this request"}</strong><p>{message.content}</p></div>
      </div>
    );
  }

  if (message.role === "user") {
    return (
      <div className="message-row user-row">
        <div className="user-message-wrap">
          <div className="message-author"><span>You</span><time>{message.time}</time></div>
          <div className="user-bubble">{message.content}</div>
        </div>
        <span className="user-avatar">Y</span>
      </div>
    );
  }

  const metadata = message.metadata!;
  const fromCache = metadata.resolution_source === "LLM_CACHE";
  return (
    <div className="message-row assistant-row" data-testid="assistant-message">
      <span className="assistant-avatar"><Sparkles size={14} /></span>
      <div className="assistant-message-wrap">
        <div className="message-author"><span>Lyftr AI</span><time>{message.time}</time></div>
        <div className="assistant-bubble">{message.content}</div>
        <div className="reply-metadata">
          <div className={`source-badge ${fromCache ? "cached" : "live"}`}>
            <span className="source-dot" />
            <span data-testid="meta-source">{metadata.resolution_source}</span>
          </div>
          <span className="metadata-divider" />
          <span className="metadata-item model-item" title={metadata.model}><Sparkles size={12} /><span data-testid="meta-model">{metadata.model}</span></span>
          <span className="metadata-item"><Clock3 size={12} /><span data-testid="meta-latency">{metadata.latency_ms} ms</span></span>
          <span className="metadata-item token-item"><ArrowDownRight size={12} /><span data-testid="meta-input-tokens">{metadata.input_tokens}</span><ArrowUpRight size={12} /><span data-testid="meta-output-tokens">{metadata.output_tokens}</span><span className="token-label">tokens</span></span>
          {metadata.fallback_attempts > 0 && (
          <span className="fallback-badge"><Zap size={12} /> {metadata.key_id} after {metadata.fallback_attempts} {metadata.fallback_attempts === 1 ? "retry" : "retries"}</span>
          )}
        </div>
        <div className="message-footnote">
          <span>{metadata.key_id ? `Answered by ${metadata.key_id}` : "Served from response cache"}</span>
          <span>·</span>
          <span>{metadata.fallback_attempts ? "Fallback succeeded" : "No retries needed"}</span>
        </div>
      </div>
    </div>
  );
}

export default App;

