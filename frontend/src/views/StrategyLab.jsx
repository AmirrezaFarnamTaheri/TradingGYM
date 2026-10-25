import { useState } from "react";
import { useT } from "../i18n";
import { request, money, num } from "../services";
export default function StrategyLab() {
  const t = useT();
  const [bars, setBars] = useState([]),
    [strategy, setStrategy] = useState("ema_cross"),
    [period, setPeriod] = useState(14),
    [result, setResult] = useState(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function load(event) {
    setError("");
    setResult(null);
    setBars([]);
    try {
      const file = event.target.files[0];
      if (!file) return;
      if (file.size > 1000000) throw Error(t("File exceeds 1 MB."));
      const text = await file.text();
      const lines = text.trim().split(/\r?\n/);
      const headers = lines
        .shift()
        .split(",")
        .map((x) => x.trim());
      if (headers.join(",") !== "at,open,high,low,close,volume")
        throw Error(t("CSV columns must be at,open,high,low,close,volume."));
      const rows = lines.map((line) => {
        const values = line.split(",");
        if (values.length !== 6) throw Error(t("Invalid CSV row."));
        return Object.fromEntries(
          headers.map((key, i) => [key, Number(values[i])]),
        );
      });
      if (
        rows.length < 30 ||
        rows.length > 2000 ||
        rows.some((row) => Object.values(row).some((v) => !Number.isFinite(v)))
      )
        throw Error(t("Provide 30–2000 finite OHLCV rows."));
      setBars(rows);
    } catch (e) {
      setError(e.message);
    }
  }
  async function run(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      setResult(
        await request("/strategy-lab", {
          method: "POST",
          body: JSON.stringify({ bars, strategy, period: Number(period) }),
        }),
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">{t("READ-ONLY RESEARCH")}</p>
          <h1>{t("Strategy lab")}</h1>
          <p>
            {t(
              "Inspect actual OHLCV data with causal next-open replay. No orders are sent.",
            )}
          </p>
        </div>
      </div>
      <section className="surface">
        <form onSubmit={run}>
          <label>
            {t("Import market CSV")}
            <input type="file" accept=".csv,text/csv" onChange={load} />
          </label>
          <p className="fine-print">
            {t(
              "Unix timestamps in seconds. Required columns: at,open,high,low,close,volume. No sample data is supplied.",
            )}
          </p>
          <p>
            {num(bars.length, 0)} {t("records")}
          </p>
          <label>
            {t("Strategy")}
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
            >
              <option value="ema_cross">{t("EMA crossover")}</option>
              <option value="rsi_reversion">{t("RSI reversion")}</option>
            </select>
          </label>
          <label>
            {t("Indicator period")}
            <input
              type="number"
              min="2"
              max="100"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
            />
          </label>
          <p>
            {t(
              "Replay assumptions: 10 bps fees, 2 bps slippage, 10% allocation, 10,000 starting capital.",
            )}
          </p>
          <button className="btn btn-primary" disabled={busy || !bars.length}>
            {busy ? t("Running…") : t("Run causal replay")}
          </button>
        </form>
        {error && <p role="alert">{error}</p>}
      </section>
      {result && (
        <section className="surface">
          <h2>{t("Research result")}</h2>
          <div className="stat-grid">
            <article className="stat-tile">
              <span>{t("Final equity")}</span>
              <strong>{money(result.report.final_equity_base)}</strong>
            </article>
            <article className="stat-tile">
              <span>{t("Net return")}</span>
              <strong>{num(result.report.net_return_fraction * 100)}%</strong>
            </article>
            <article className="stat-tile">
              <span>{t("Maximum drawdown")}</span>
              <strong>{num(result.report.max_drawdown_fraction * 100)}%</strong>
            </article>
            <article className="stat-tile">
              <span>{t("Executions")}</span>
              <strong>{num(result.report.trade_count, 0)}</strong>
            </article>
          </div>
          <p className="fine-print">
            {t(
              "Exploratory in-sample results; not a qualified trading policy. Source hash identifies supplied bytes, not exchange authenticity.",
            )}
          </p>
          <p className="mono">{result.report.input_sha256}</p>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {["Time", "Close", "ADX", "RSI", "Fast EMA", "Slow EMA"].map(
                    (s) => (
                      <th key={s}>{t(s)}</th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {result.indicators.slice(-30).map((row) => (
                  <tr key={row.at}>
                    <td>{new Date(row.at * 1000).toLocaleString()}</td>
                    {["close", "adx", "rsi", "fast_ema", "slow_ema"].map(
                      (key) => (
                        <td key={key}>{num(row[key])}</td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
