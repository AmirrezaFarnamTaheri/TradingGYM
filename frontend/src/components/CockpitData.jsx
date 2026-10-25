import { useT } from "../i18n";
import Icon from "./Icon";
import { useState } from "react";
import { money, num } from "../services";
export function EquityChart({ points, initial, mode = "paper" }) {
  const t = useT();
  const [selected, setSelected] = useState(null);
  if (points.length < 2)
    return (
      <div className="chart-empty">
        <Icon name="chart" size={28} />
        <h3>{t("Waiting for verified account data.")}</h3>
        <p>
          {mode === "observe"
            ? t(
                "Observation mode does not estimate a portfolio curve from market quotes.",
              )
            : t("Connect Abantether in Settings to load your account.")}
        </p>
        <span>
          {t(
            "No account value or trading return is estimated from missing data.",
          )}
        </span>
      </div>
    );
  const values = points.map((p) => p.equity),
    min = Math.min(...values, ...(mode === "paper" ? [initial] : [])),
    max = Math.max(...values, ...(mode === "paper" ? [initial] : [])),
    range = Math.max(max - min, Math.abs(max) * 0.002, 0.01);
  const index = Math.min(
    values.length - 1,
    Math.max(0, selected ?? values.length - 1),
  );
  const line = values
    .map(
      (v, i) =>
        `${40 + (i / (values.length - 1)) * 900},${205 - ((v - min) / range) * 160}`,
    )
    .join(" ");
  return (
    <div className="equity-chart">
      <label className="fine-print">
        {t("Inspect recorded equity")}{" "}
        <input
          aria-label={t("Inspect recorded equity")}
          type="range"
          min="0"
          max={values.length - 1}
          value={index}
          onChange={(e) => setSelected(Number(e.target.value))}
        />
      </label>
      <p className="mono">
        {new Date(points[index].at * 1000).toLocaleString()} ·{" "}
        {money(values[index])} · {t("High water")}: {money(Math.max(...values))}
      </p>
      <svg
        viewBox="0 0 1000 250"
        role="img"
        aria-label={`Account equity from ${money(values[0])} to ${money(values.at(-1))}`}
        onPointerMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          setSelected(
            Math.round(
              Math.min(
                1,
                Math.max(
                  0,
                  (((e.clientX - rect.left) / rect.width) * 1000 - 40) / 900,
                ),
              ) *
                (values.length - 1),
            ),
          );
        }}
      >
        <defs>
          <linearGradient id="equity-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#35c6a0" stopOpacity=".18" />
            <stop offset="1" stopColor="#35c6a0" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[45, 98, 151, 205].map((y) => (
          <line
            key={y}
            x1="40"
            y1={y}
            x2="940"
            y2={y}
            stroke="#24313b"
            strokeDasharray="3 5"
          />
        ))}
        <polygon points={`40,220 ${line} 940,220`} fill="url(#equity-fill)" />
        {mode === "paper" && (
          <line
            x1="40"
            x2="940"
            y1={205 - ((initial - min) / range) * 160}
            y2={205 - ((initial - min) / range) * 160}
            stroke="#85939f"
            strokeDasharray="6 4"
          >
            <title>{t("Initial paper capital")}</title>
          </line>
        )}
        <line
          x1={40 + (index / (values.length - 1)) * 900}
          x2={40 + (index / (values.length - 1)) * 900}
          y1="35"
          y2="220"
          stroke="#85939f"
        />
        <circle
          cx={40 + (index / (values.length - 1)) * 900}
          cy={205 - ((values[index] - min) / range) * 160}
          r="4"
          fill="#51d7b3"
        >
          <title>{money(values[index])}</title>
        </circle>
        <polyline
          points={line}
          fill="none"
          stroke="#51d7b3"
          strokeWidth="2.2"
          vectorEffect="non-scaling-stroke"
        />
        <text x="40" y="244" fill="#85939f" fontSize="11">
          {new Date(points[0].at * 1000).toLocaleDateString()}
        </text>
        <text x="940" y="244" textAnchor="end" fill="#85939f" fontSize="11">
          {new Date(points.at(-1).at * 1000).toLocaleTimeString()}
        </text>
      </svg>
    </div>
  );
}
export function RiskMeter({ label, value, limit }) {
  const t = useT();
  const ratio = Math.min(100, Math.max(0, (value / limit) * 100));
  return (
    <div className="risk-meter">
      <div>
        <span>{t(label)}</span>
        <strong>
          {value == null ? "—" : num(value * 100) + "%"}{" "}
          <small>/ {num(limit * 100)}%</small>
        </strong>
      </div>
      <div className="meter-track">
        <span
          style={{
            width: `${ratio}%`,
          }}
          className={ratio > 75 ? "danger" : ""}
        />
      </div>
    </div>
  );
}
export function TradeTable({ trades, compact = false }) {
  const t = useT();
  const [page, setPage] = useState(0);
  const last = Math.max(0, Math.ceil(trades.length / 15) - 1);
  const current = Math.min(page, last);
  if (!trades.length)
    return (
      <div className="table-empty">
        <Icon name="clock" />
        <div>
          <strong>{t("No executions yet")}</strong>
          <p>{t("Paper fills and their modeled costs will appear here.")}</p>
        </div>
      </div>
    );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>{t("Time")}</th>
            <th>{t("Market / side")}</th>
            <th>{t("Quantity")}</th>
            <th>{t("Fill price")}</th>
            <th>{t("Fee")}</th>
            {!compact && <th>{t("Realized P&L*")}</th>}
            <th>{t("Reason")}</th>
          </tr>
        </thead>
        <tbody>
          {trades
            .slice(compact ? 0 : current * 15, compact ? 5 : (current + 1) * 15)
            .map((t) => (
              <tr key={t.id}>
                <td className="muted">{new Date(t.at).toLocaleTimeString()}</td>
                <td>
                  <strong>{t.symbol}</strong>
                  <span className={`side-label ${t.side}`}>{t.side}</span>
                </td>
                <td className="mono">{num(t.quantity, 6)}</td>
                <td className="mono">{money(t.price)}</td>
                <td className="mono muted">{money(t.fee)}</td>
                {!compact && (
                  <td
                    className={
                      Number(t.realized_pnl) >= 0 ? "positive" : "negative"
                    }
                  >
                    {money(t.realized_pnl)}
                  </td>
                )}
                <td className="muted">{t.reason.replaceAll("_", " ")}</td>
              </tr>
            ))}
        </tbody>
      </table>
      {!compact && trades.length > 15 && (
        <div className="history-pagination">
          <button
            className="btn"
            disabled={current === 0}
            onClick={() => setPage(current - 1)}
          >
            {t("Newer fills")}
          </button>
          <span>
            {t("Page")}
            {current + 1}
            {t("of")}
            {last + 1}
          </span>
          <button
            className="btn"
            disabled={current === last}
            onClick={() => setPage(current + 1)}
          >
            {t("Older fills")}
          </button>
        </div>
      )}
    </div>
  );
}
