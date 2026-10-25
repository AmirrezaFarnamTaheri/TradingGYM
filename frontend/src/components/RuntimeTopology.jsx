import { useT } from "../i18n";
export default function RuntimeTopology({ runtime }) {
  const t = useT();
  if (!runtime) return null;
  return (
    <section className="surface runtime-topology">
      <div className="surface-heading">
        <h2>{t("Tools, heads & decision route")}</h2>
        <span className="pill">{t("RUNTIME")}</span>
      </div>
      <div className="trace-grid">
        <div>
          <h3>{t("Available tools")}</h3>
          <ul className="tool-list">
            {runtime.tools.map((tool) => (
              <li key={t(tool.name)}>
                <strong>{t(tool.name)}</strong>
                <span>
                  {t(tool.status.replaceAll("_", " "))} · {t(tool.access)}
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>{t("Signal heads")}</h3>
          <ul className="tool-list">
            {runtime.heads.map((head) => (
              <li key={head.name}>
                <strong>{t(head.name)}</strong>
                <span>
                  {head.active
                    ? t("Active")
                    : head.enabled
                      ? t("Ready")
                      : t("Disabled")}{" "}
                  · {t(head.type.replaceAll("_", " "))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <h3>{t("Decision route")}</h3>
      {runtime.gateway_routes?.map((gateway) => (
        <div className="gateway-route" key={gateway.role}>
          <strong>
            {gateway.role === "llm"
              ? t("LLM planner keys")
              : t("Decision-1 gateway keys")}
          </strong>
          <p>
            {gateway.enabled ? (
              <>
                <bdi>{gateway.model}</bdi> · <bdi>{gateway.endpoint}</bdi>
              </>
            ) : (
              t("Disabled")
            )}
          </p>
          <p>
            {gateway.last_key_id
              ? gateway.keys.find((key) => key.id === gateway.last_key_id)
                  ?.label || gateway.last_key_id
              : t("No model request yet")}
          </p>
        </div>
      ))}
      <ol className="decision-route">
        {runtime.route.map((stage, i) => (
          <li key={i}>{t(stage)}</li>
        ))}
      </ol>
    </section>
  );
}
