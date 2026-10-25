import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import App from "./App";

const settings = {
  symbols: ["BTCUSDT"],
  initial_cash: 10000,
  max_drawdown: 0.05,
  max_daily_loss: 0.02,
  max_position_fraction: 0.2,
  fee_bps: 10,
  slippage_bps: 2,
  stop_loss_fraction: 0.03,
  planner_interval_seconds: 300,
  poll_seconds: 10,
  max_feed_age_seconds: 360,
  execution_mode: "observe",
};
const baseline = {
  broker: {
    status: "connected",
    account_connected: false,
    quotes: [],
    balances: [],
  },
  status: "stopped",
  mode: "observe",
  source: "not_started",
  paused: false,
  halted: false,
  error: null,
  heartbeat: null,
  equity: 10000,
  cash: 10000,
  drawdown: 0,
  daily_loss: 0,
  fees: 0,
  regime: "warming_up",
  arm_weights: { rules: 1, stats: 1 },
  planner_note: "Local planner",
  positions: [],
  signals: [],
  equity_curve: [],
  events: [],
  trades: [],
  settings,
  learning: {
    mode: "observation_only",
    detail: "No trained challenger evaluated.",
  },
};
let container, root, state, requests;
beforeEach(() => {
  state = structuredClone(baseline);
  requests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options = {}) => {
      requests.push({ url, options });
      if (url.includes("/history") || url.includes("/decisions"))
        return { ok: true, json: async () => ({ total: 0, items: [] }) };
      if (url.endsWith("/credentials"))
        return {
          ok: true,
          json: async () => ({
            llm: [],
            decision1: [],
            abantether_configured: false,
          }),
        };
      if (url.endsWith("/start"))
        state = { ...state, status: "running", source: "broker" };
      if (url.endsWith("/pause")) state = { ...state, paused: true };
      if (url.endsWith("/flatten"))
        state = { ...state, halted: true, status: "halted", positions: [] };
      return {
        ok: true,
        status: 200,
        json: async () =>
          url.endsWith("/settings")
            ? {
                ...settings,
                broker_configured: false,
                llm_configured: false,
                live_configured: false,
              }
            : structuredClone(state),
      };
    }),
  );
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});
async function mount() {
  await act(async () => root.render(<App />));
  await settle();
}
async function settle() {
  await act(async () => new Promise((resolve) => setTimeout(resolve, 30)));
}
const button = (label) =>
  container.querySelector(`button[aria-label="${label}"]`) ||
  [...container.querySelectorAll("button")].find((b) =>
    b.textContent.includes(label),
  );

it("boots directly into three accessible cockpit views with real empty states", async () => {
  await mount();
  expect(container.querySelectorAll("nav button")).toHaveLength(3);
  expect(container.querySelector("h1").textContent).toBe("Live monitor");
  expect(container.querySelector("input[type=password]")).toBeNull();
  expect(container.textContent).toContain("Waiting for verified account data");
  expect(requests[0].url).toBe("/api/v1/agent");
  expect(requests[0].options.headers["X-API-Key"]).toBeUndefined();
  await act(async () => button("Experience").click());
  expect(container.querySelector("h1").textContent).toBe("Experience ledger");
  await act(async () => button("Settings").click());
  await settle();
  expect(container.querySelector("h1").textContent).toBe("Agent settings");
  expect(container.textContent).toContain("Not configured");
  for (const input of container.querySelectorAll('input[type="number"]')) {
    expect(input.checkValidity()).toBe(true);
  }
});

it("starts only the selected paper source and can pause entries", async () => {
  await mount();
  await act(async () => button("Start robot").click());
  await settle();
  const start = requests.find((r) => r.url.endsWith("/start"));
  expect(JSON.parse(start.options.body)).toEqual({
    source: "broker",
    mode: "paper",
  });
  expect(container.textContent).toContain(
    "Actual Abantether market observations",
  );
  await act(async () => button("Pause entries").click());
  await settle();
  expect(requests.some((r) => r.url.endsWith("/pause"))).toBe(true);
  expect(container.textContent).toContain("Resume entries");
});

it("keeps emergency controls present across views and confirms flattening", async () => {
  state.mode='paper';
  await mount();
  await act(async () => button("Experience").click());
  await act(async () => button("Flatten paper").click());
  expect(container.querySelector("[role=dialog]")).not.toBeNull();
  expect(document.activeElement.textContent).toContain("Keep running");
  await act(async () =>
    document.activeElement.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Tab",
        shiftKey: true,
        bubbles: true,
        cancelable: true,
      }),
    ),
  );
  expect(document.activeElement.textContent).toContain("Flatten & halt");
  expect(requests.some((r) => r.url.endsWith("/flatten"))).toBe(false);
  await act(async () => button("Keep running").click());
  expect(container.querySelector("[role=dialog]")).toBeNull();
  await act(async () => button("Flatten paper").click());
  await act(async () => button("Flatten & halt").click());
  await settle();
  expect(requests.filter((r) => r.url.endsWith("/flatten"))).toHaveLength(1);
  expect(container.textContent).toContain("RISK HALTED");
});

it("opens the exact historical signals and risk route bound to a fill", async()=>{
  const priorFetch=fetch;
  const decision={id:'test-decision-1',at:1704067200,symbol:'BTCUSDT',source:'test_fixture',mode:'paper',outcome:'paper_eligible',action:'long',target_fraction:.1,rationale:'Weighted positive momentum',market:{close:43000,previous_close:42000},arms:[{name:'rules',action:'long',signal:.1,confidence:.8,weight:1,rationale:'Observed momentum increased'}],planner:{rationale:'Trend up at decision time'},risk:{position_cap:.2,blocked_by:[]},route:['closed_bar','rules','financial_risk','paper_eligible']};
  vi.stubGlobal('fetch',vi.fn(async(url,options)=>{
    if(url.includes('/history?')) return {ok:true,json:async()=>({total:1,items:[{id:'confirmed-test-fill',at:'2024-01-01T00:00:00Z',symbol:'BTCUSDT',side:'buy',quantity:'0.01',price:'43000',mode:'broker',decision_id:decision.id}]})};
    if(url.includes('/decision?id='))return {ok:true,json:async()=>decision};
    return priorFetch(url,options);
  }));
  await mount();
  await act(async()=>button('Experience').click());
  await settle();
  await act(async()=>button('View decision').click());
  await settle();
  expect(container.textContent).toContain('Observed momentum increased');
  expect(container.textContent).toContain('Trend up at decision time');
  expect(container.textContent).toContain('financial risk');
});

it("shows an offline state instead of inventing account values", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("offline"));
  await mount();
  await act(async () => new Promise((resolve) => setTimeout(resolve, 1200)));
  expect(container.textContent).toContain("Backend is not connected");
  expect(container.textContent).not.toContain("$10,000.00");
});

it("switches the cockpit and settings to Persian with right-to-left layout", async () => {
  await mount();
  await act(async () => {
    const language = container.querySelector('select[aria-label="Language"]');
    language.value = "fa";
    language.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(document.documentElement.dir).toBe("rtl");
  expect(container.querySelector("h1").textContent).toContain("پایش زنده");
  const settingsButton = container.querySelector(
    'button[aria-label="تنظیمات"]',
  );
  await act(async () => settingsButton.click());
  await settle();
  expect(container.textContent).toContain("کلیدهای API و گردش کلیدها");
  expect(
    container.querySelectorAll("input[type=password]").length,
  ).toBeGreaterThan(2);
  expect(localStorage.getItem("cockpit-language")).toBe("fa");
});
