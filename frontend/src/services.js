import { currentLocale } from "./i18n";
export async function request(path, options = {}) {
  const response = await fetch("/api/v1/agent" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "The request could not be completed.",
    );
  return data;
}
export const money = (v) =>
  v !== null && v !== undefined && Number.isFinite(Number(v))
    ? Number(v).toLocaleString(currentLocale(), {
        style: "currency",
        currency: "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    : "—";
export const num = (v, d = 2) =>
  v !== null && v !== undefined && Number.isFinite(Number(v))
    ? Number(v).toLocaleString(currentLocale(), { maximumFractionDigits: d })
    : "—";
export const when = (v) =>
  v
    ? new Date(typeof v === "number" ? v * 1000 : v).toLocaleString(
        currentLocale(),
      )
    : "Not observed";
