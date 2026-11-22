// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

type MockResponse = Pick<Response, "ok" | "status" | "headers" | "json">;

function jsonResponse(
  body: unknown,
  status = 200,
  responseHeaders: Record<string, string> = {},
): MockResponse {
  const normalizedHeaders = new Map(
    Object.entries(responseHeaders).map(([name, value]) => [name.toLowerCase(), value]),
  );
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (name: string) => normalizedHeaders.get(name.toLowerCase()) ?? null,
    } as unknown as Headers,
    json: async () => body,
  };
}

const healthyResponse = () =>
  jsonResponse({
    status: "ok",
    keys_configured: 2,
    cache_enabled: true,
    database_ready: true,
    failure_simulation_enabled: true,
  });

function installFetch(chatResponse: () => MockResponse) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/health")) return healthyResponse();
    if (url.endsWith("/chat")) return chatResponse();
    return jsonResponse({ detail: "Not found" }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("chat UI contract", () => {
  it("sends a message and renders Markdown plus per-reply metadata", async () => {
    const reply =
      "A **bold** answer.\n\n| Feature | Meaning |\n| --- | --- |\n| API | Connects software |";
    const fetchMock = installFetch(() =>
      jsonResponse({
        session_id: "s-test",
        reply,
        metadata: {
          resolution_source: "LIVE_INFERENCE",
          model: "gemini/gemini-3.5-flash-lite",
          latency_ms: 120,
          input_tokens: 31,
          output_tokens: 12,
          key_id: "key_1",
          fallback_attempts: 0,
        },
      }),
    );
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    await user.type(input, "Define an API");
    await user.keyboard("{Enter}");

    const assistantMessage = await screen.findByTestId("assistant-message");
    expect(input.value).toBe("");
    expect(assistantMessage.querySelector("strong")?.textContent).toBe("bold");
    expect(assistantMessage.querySelector("table")).not.toBeNull();
    expect(screen.getByTestId("meta-source").textContent).toBe("LIVE_INFERENCE");
    expect(screen.getByTestId("meta-model").textContent).toBe(
      "gemini/gemini-3.5-flash-lite",
    );
    expect(screen.getByTestId("meta-latency").textContent).toBe("120 ms");
    expect(screen.getByTestId("meta-input-tokens").textContent).toBe("31");
    expect(screen.getByTestId("meta-output-tokens").textContent).toBe("12");

    const chatCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/chat"));
    expect(chatCall).toBeDefined();
    const body = JSON.parse(String(chatCall?.[1]?.body)) as {
      session_id: string;
      message: string;
    };
    expect(body.session_id).toMatch(/^s-/);
    expect(body.message).toBe("Define an API");
  });

  it("shows validation errors in the conversation instead of a blank screen", async () => {
    installFetch(() =>
      jsonResponse(
        { detail: [{ loc: ["body", "message"], msg: "Field required" }] },
        422,
      ),
    );
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByTestId("chat-input"), "A valid-looking prompt");
    await user.keyboard("{Enter}");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("message: Field required");
  });

  it("shows provider outages as readable errors", async () => {
    installFetch(() =>
      jsonResponse(
        { detail: "all providers unavailable" },
        503,
        { "X-Provider-Failure": "provider_timeout" },
      ),
    );
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByTestId("chat-input"), "Explain an API");
    await user.keyboard("{Enter}");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("did not respond before the timeout");
  });

  it("explains a rate limit and its retry delay", async () => {
    installFetch(() =>
      jsonResponse(
        { detail: "rate limit exceeded" },
        429,
        { "Retry-After": "23" },
      ),
    );
    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByTestId("chat-input"), "Explain an API");
    await user.keyboard("{Enter}");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("wait about 23 seconds");
    expect(alert.textContent).toContain("This turn was not saved.");
    expect(alert.querySelector("button")?.textContent).toContain("Try again");
  });
});
