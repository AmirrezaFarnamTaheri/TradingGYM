import { useT } from "../i18n";
import RobotHistory from "../components/RobotHistory";
import Icon from "../components/Icon";
import { useState } from "react";
import { money, num, when } from "../services";
import { TradeTable } from "../components/CockpitData";
export default function Experience({ data }) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">{t("LEARNING & ATTRIBUTION")}</p>
          <h1>{t("Experience ledger")}</h1>
          <p>
            {t("Observed decisions, execution costs, and settled outcomes.")}
          </p>
        </div>
        <span className="pill">{t("LEARNING & ATTRIBUTION")}</span>
      </div>
      <div className="learning-banner">
        <Icon name="cpu" size={26} />
        <div>
          <h2>{t("Observe first. Improve with evidence.")}</h2>
          <p>{t(data.learning.detail)}</p>
        </div>
        <span className="pill">
          {t(data.learning.mode.replaceAll("_", " "))}
        </span>
      </div>
      <section className="surface">
        <div className="surface-heading">
          <h2>{t("Shadow comparisons")}</h2>
          <span className="pill">{t(data.learning.status || "disabled")}</span>
        </div>
        {!data.learning.evaluations?.length ? (
          <div className="table-empty">
            {t(
              "No challenger has been evaluated. Broker history must meet the training minimum first.",
            )}
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{t("Market")}</th>
                  <th>{t("Held-out bars")}</th>
                  <th>{t("Incumbent equity")}</th>
                  <th>{t("Challenger equity")}</th>
                  <th>{t("PSR / DSR")}</th>
                  <th>{t("Paper result")}</th>
                </tr>
              </thead>
              <tbody>
                {[...data.learning.evaluations].reverse().map((trial, i) => (
                  <tr key={`${trial.at}-${i}`}>
                    <td>{trial.symbol}</td>
                    <td>{trial.holdout_bars}</td>
                    <td>{money(trial.incumbent_equity)}</td>
                    <td>{money(trial.challenger_equity)}</td>
                    <td>
                      {num(trial.probabilistic_sharpe * 100)}% /{" "}
                      {num(trial.deflated_sharpe * 100)}%
                    </td>
                    <td>
                      {trial.promoted
                        ? t("Activated in paper")
                        : t("Incumbent retained")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <RobotHistory />
      <section className="surface">
        <div className="surface-heading">
          <h2>{t("Agent timeline")}</h2>
          <Icon name="clock" size={18} />
        </div>
        {!data.events.length ? (
          <div className="table-empty">
            {t("No activity recorded. Start a paper agent from the monitor.")}
          </div>
        ) : (
          <ol className="timeline">
            {data.events.slice(0, expanded ? 100 : 12).map((e, i) => (
              <li key={`${e.at}-${i}`}>
                <span className={`timeline-dot ${e.kind}`} />
                <time>{when(e.at)}</time>
                <p>{e.message}</p>
              </li>
            ))}
          </ol>
        )}
        {data.events.length > 12 && (
          <button
            className="text-button"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? t("Show recent activity") : t("Show more activity")}
          </button>
        )}
      </section>
    </>
  );
}
