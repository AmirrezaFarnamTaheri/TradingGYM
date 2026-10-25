// Test environment bootstrap for Vitest (jsdom).
import { beforeEach } from "vitest";
import { JSDOM } from "jsdom";

// Node 25+ exposes an incomplete Web Storage global. Browser tests must use
// the actual jsdom storage implementation, including its origin checks.
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: new JSDOM("", { url: "http://localhost" }).window.localStorage,
});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => window.localStorage.clear());
