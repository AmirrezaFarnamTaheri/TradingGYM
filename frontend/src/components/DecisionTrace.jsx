import { useT } from "../i18n";
import { money, num, when } from "../services";
export default function DecisionTrace({ decision }) {
  const t = useT();
  if (!decision)
    return (
      <p className="fine-print">
        {t(
          "No robot decision was recorded for this broker order. Attribution is unavailable.",
        )}
      </p>
    );
  return (
    <section className="decision-trace" aria-label={t("Decision details")}>
      <div className="surface-heading">
        <h3>{t("Decision details")}</h3>
        <span className="mono" dir="ltr">
          {decision.id}
        </span>
      </div>
      <p>
        <strong>{decision.symbol}</strong> · {when(decision.at)} ·{" "}
        {decision.source}
      </p>
      <p className="pill">{t(decision.outcome || decision.mode)}</p>
      <ol className="decision-route">
        {decision.route.map((step, i) => (
          <li key={i}>{step.replaceAll("_", " ")}</li>
        ))}
      </ol>
      <div className="trace-grid">
        <div>
          <h3>{t("Observed market")}</h3>
          <p>
            {t("Close:")}
            {money(decision.market?.close)}
          </p>
          <p>
            {t("Previous close:")}
            {money(decision.market?.previous_close)}
          </p>
        </div>
        <div>
          <h3>{t("Final target")}</h3>
          <p>
            {decision.action} · {num(decision.target_fraction * 100)}%
          </p>
          <p>{decision.rationale}</p>
        </div>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{t("Signal head")}</th>
              <th>{t("Action")}</th>
              <th>{t("Target")}</th>
              <th>{t("Confidence")}</th>
              <th>{t("Weight")}</th>
              <th>{t("Signal rationale")}</th>
            </tr>
          </thead>
          <tbody>
            {decision.arms.map((arm) => (
              <tr key={arm.name}>
                <td>{arm.name}</td>
                <td>{arm.action}</td>
                <td>{num(arm.signal * 100)}%</td>
                <td>{num(arm.confidence * 100)}%</td>
                <td>{num(arm.weight)}</td>
                <td className="rationale-cell">{arm.rationale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>{t("Planner at decision time")}</h3>
      <p>{decision.planner?.rationale || t("Financial risk override")}</p>
      <h3>{t("Risk checks at decision time")}</h3>
      <dl className="risk-checks">
        {Object.entries(decision.risk || {}).map(([key, value]) => (
          <div key={key}>
            <dt>{t(key.replaceAll("_", " "))}</dt>
            <dd dir="ltr">{value === null ? "—" : String(value)}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
