import { useT } from "../i18n";
import RuntimeTopology from "../components/RuntimeTopology";
import Icon from "../components/Icon";
import { money, num } from "../services";
import { EquityChart, RiskMeter, TradeTable } from "../components/CockpitData";
export default function Monitor({ data, act, pending, onExperience }) {
  const t = useT();
  const running = data.status === "running";
  const pnl =
    data.mode === "paper" ? data.equity - data.settings.initial_cash : null;
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">{t("AUTONOMOUS OPERATIONS")}</p>
          <h1>
            {t("Live monitor")}
            <span className="title-dot" />
          </h1>
          <p>
            {t("Your agent, portfolio, and decision loop. One shared view.")}
          </p>
        </div>
        <div className="run-controls">
          <span className="pill">{t("ABANTETHER")}</span>
          {running ? (
            <>
              <button
                className="btn"
                disabled={pending}
                onClick={() => act(data.paused ? "resume" : "pause")}
              >
                <Icon name={data.paused ? "play" : "clock"} size={16} />
                {data.paused ? t("Resume entries") : t("Pause entries")}
              </button>
              <button
                className="btn"
                disabled={pending}
                onClick={() => act("stop")}
              >
                {t("Stop agent")}
              </button>
            </>
          ) : (
            <button
              className="btn btn-primary"
              disabled={pending || data.halted}
              onClick={() =>
                act("start", {
                  source: "broker",
                  mode: data.mode || "observe",
                })
              }
            >
              <Icon name="play" size={15} />
              {t("Start robot")}
            </button>
          )}
        </div>
      </div>
      <div className="source-notice">
        <Icon name="flask" size={16} />
        <span>
          {data.broker?.status === "connected"
            ? t("Actual Abantether market observations")
            : t(
                "Abantether is not connected. Add your API key in Settings and check the connection.",
              )}
        </span>
        <span className="notice-right">
          {data.mode === "paper" ? t("PAPER EXECUTION") : t("OBSERVATION ONLY")}
        </span>
      </div>
      {data.halted && (
        <div className="alert alert-risk">
          <Icon name="shield" />
          <div>
            <strong>{t("Risk halt is latched.")}</strong>
            <p>
              {t(
                "Stop the agent, inspect the account, and reset the halt from Settings before restarting.",
              )}
            </p>
          </div>
        </div>
      )}
      {data.error && (
        <div className="alert" role="alert">
          {data.error}
        </div>
      )}
      <div className="stat-grid">
        {data.broker?.quotes?.map((quote) => {
          const pending = data.broker.pending_bars?.[quote.symbol];
          return (
            <article className="stat-tile" key={quote.symbol}>
              <span>{quote.symbol}</span>
              <strong>{money(quote.price)}</strong>
              <small>
                {t("Spread")}: {num(quote.spread_bps)} bps ·{" "}
                {num(data.broker.bar_counts?.[quote.symbol] || 0, 0)} / 14{" "}
                {t("Observed bars")}
              </small>
              <small>
                {t("Last observation")}:{" "}
                {new Date(quote.at * 1000).toLocaleTimeString()} ·{" "}
                {t(data.broker.status)}
              </small>
              {pending && (
                <>
                  <small>
                    {t("Partial observed interval")}:{" "}
                    {num(
                      Math.min(
                        100,
                        Math.max(0, ((quote.at - pending.bucket) / 300) * 100),
                      ),
                      0,
                    )}
                    %
                  </small>
                  <progress
                    max="100"
                    value={Math.min(
                      100,
                      Math.max(0, ((quote.at - pending.bucket) / 300) * 100),
                    )}
                    aria-label={t("Partial observed interval")}
                  />
                  <small>
                    O {money(pending.open)} · H {money(pending.high)} · L{" "}
                    {money(pending.low)} · C {money(pending.price)}
                  </small>
                </>
              )}
            </article>
          );
        })}
        {[
          [t("Portfolio equity"), money(data.equity), t("USDT account")],
          [
            t("Net change"),
            `${pnl !== null && pnl >= 0 ? "+" : ""}${money(pnl)}`,
            pnl === null
              ? t("Awaiting account reconciliation")
              : `${num((pnl / data.settings.initial_cash) * 100)}% ${t("since account creation")}`,
          ],
          [
            t("Available cash"),
            money(data.cash),
            t("Shared across all markets"),
          ],
          [
            t("Execution fees"),
            money(data.fees),
            `${num(data.trades.length, 0)} ${t("recent fills recorded")}`,
          ],
        ].map(([label, value, note], i) => (
          <article className="stat-tile" key={label}>
            <span>
              {label}
              <Icon
                name={["chart", "activity", "layers", "settings"][i]}
                size={16}
              />
            </span>
            <strong
              className={
                i === 1 && pnl !== null
                  ? pnl >= 0
                    ? "positive"
                    : "negative"
                  : ""
              }
            >
              {value}
            </strong>
            <small>{note}</small>
          </article>
        ))}
      </div>
      <div className="monitor-top">
        <section className="surface equity-surface">
          <div className="surface-heading">
            <h2>{t("Portfolio performance")}</h2>
            <span className="pill">
              {data.mode === "paper"
                ? t("PAPER / ABANTETHER")
                : t("ABANTETHER ACCOUNT")}
            </span>
          </div>
          <div className="chart-heading">
            <strong>{money(data.equity)}</strong>
            {pnl !== null && (
              <span className={pnl >= 0 ? "positive" : "negative"}>
                {pnl !== null && pnl >= 0 ? "+" : ""}
                {num(
                  pnl === null
                    ? null
                    : (pnl / data.settings.initial_cash) * 100,
                )}
                %
              </span>
            )}
            <small>
              {data.mode === "paper"
                ? t("Fees and modeled slippage included")
                : t("Only verified broker account values are displayed")}
            </small>
          </div>
          <EquityChart
            mode={data.mode}
            points={data.equity_curve}
            initial={data.settings.initial_cash}
          />
        </section>
        <section className="surface risk-surface">
          <div className="surface-heading">
            <h2>{t("Risk envelope")}</h2>
            <Icon name="shield" size={18} />
          </div>
          <div className={`risk-state ${data.halted ? "negative" : ""}`}>
            <span className="status-dot" />
            {data.halted
              ? t("RISK HALT")
              : data.status === "running"
                ? t("MONITORING")
                : t("STANDBY")}
          </div>
          <RiskMeter
            label="Peak drawdown"
            value={data.drawdown}
            limit={data.settings.max_drawdown}
          />
          <RiskMeter
            label="Daily loss"
            value={data.daily_loss}
            limit={data.settings.max_daily_loss}
          />
          <div className="risk-setting">
            <span>{t("Per-market exposure cap")}</span>
            <strong>{num(data.settings.max_position_fraction * 100)}%</strong>
          </div>
          <div className="risk-setting">
            <span>{t("Stop-loss distance")}</span>
            <strong>{num(data.settings.stop_loss_fraction * 100)}%</strong>
          </div>
          <p className="fine-print">
            {t(
              "Paper stops execute at the next observed bar close. They cannot guarantee a price.",
            )}
          </p>
        </section>
      </div>
      <RuntimeTopology runtime={data.runtime} />
      <div className="brain-grid">
        <section className="surface">
          <div className="surface-heading">
            <h2>
              <span className="system-badge">{t("S1")}</span>
              {t("Signal engine")}
            </h2>
            <span className="muted small">{t("CLOSED-BAR DECISIONS")}</span>
          </div>
          {!data.signals.length ? (
            <div className="table-empty">
              <Icon name="activity" />
              <div>
                <strong>{t("Waiting for enough history")}</strong>
                <p>{t("The engine needs 14 closed bars per market.")}</p>
              </div>
            </div>
          ) : (
            <div className="signal-list">
              {data.signals.map((s) => (
                <div className="signal-row" key={s.symbol}>
                  <div>
                    <strong>{s.symbol}</strong>
                    <small>
                      {s.stale
                        ? "STALE · ENTRIES BLOCKED"
                        : s.action.toLowerCase() === "long"
                          ? t("LONG TARGET")
                          : t("FLAT TARGET")}
                    </small>
                  </div>
                  <div className="signal-arms">
                    {s.arms
                      .filter((a) => a.name !== "regime")
                      .map((a) => (
                        <div key={a.name}>
                          <span>{a.name}</span>
                          <div className="signal-track">
                            <i
                              className={a.signal < 0 ? "sell" : ""}
                              style={{
                                width: `${Math.min(100, Math.abs(a.signal) * 100)}%`,
                              }}
                            />
                          </div>
                          <small>{num(a.confidence * 100, 0)}%</small>
                        </div>
                      ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          <p className="fine-print">
            {t(
              "Spot paper account: bearish or neutral signals target zero exposure. Signals remain subject to pause, cooldown, and risk limits.",
            )}
          </p>
        </section>
        <section className="surface">
          <div className="surface-heading">
            <h2>
              <span className="system-badge violet">{t("S2")}</span>
              {t("Strategy planner")}
            </h2>
            <span className="pill subtle">
              {data.settings.llm_enabled ? t("MODEL ASSISTED") : t("LOCAL")}
            </span>
          </div>
          <div className="regime-label">
            {t("CURRENT REGIME")}
            <strong>{t(data.regime.replaceAll("_", " "))}</strong>
          </div>
          <div className="weight-list">
            {Object.entries(data.arm_weights).map(([arm, weight]) => (
              <div key={arm}>
                <span>{arm}</span>
                <div className="meter-track">
                  <span
                    style={{
                      width: `${weight * 100}%`,
                    }}
                  />
                </div>
                <strong>{num(weight, 1)}×</strong>
              </div>
            ))}
          </div>
          <p className="planner-note">{t(data.planner_note)}</p>
        </section>
      </div>
      <section className="surface">
        <div className="surface-heading">
          <h2>
            {t("Open positions")}
            <span className="count-badge">{data.positions.length}</span>
          </h2>
          <span className="muted small">
            {data.mode === "paper"
              ? t("SHARED PAPER ACCOUNT")
              : t("ABANTETHER ACCOUNT")}
          </span>
        </div>
        {!data.positions.length ? (
          <div className="table-empty">
            <Icon name="layers" />
            <div>
              <strong>{t("No verified holdings received")}</strong>
              <p>{t("Connect Abantether to inspect account balances.")}</p>
            </div>
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{t("Market")}</th>
                  <th>{t("Quantity")}</th>
                  <th>{t("Entry")}</th>
                  <th>{t("Last mark")}</th>
                  <th>{t("Unrealized")}</th>
                  <th>{t("Stop reference")}</th>
                </tr>
              </thead>
              <tbody>
                {data.positions.map((p) => (
                  <tr key={p.symbol}>
                    <td>
                      <strong>{p.symbol}</strong>
                      <span className="side-label buy">{t("long")}</span>
                    </td>
                    <td className="mono">{num(p.quantity, 6)}</td>
                    <td>{money(p.entry)}</td>
                    <td>{money(p.mark)}</td>
                    <td
                      className={
                        p.unrealized_pnl >= 0 ? "positive" : "negative"
                      }
                    >
                      {money(p.unrealized_pnl)}
                    </td>
                    <td className="muted">{money(p.stop_price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="surface">
        <div className="surface-heading">
          <h2>{t("Recent executions")}</h2>
          <button className="text-button" onClick={onExperience}>
            {t("View experience ledger")}
            <Icon name="arrow" size={15} />
          </button>
        </div>
        <TradeTable trades={data.trades} compact />
      </section>
    </>
  );
}
