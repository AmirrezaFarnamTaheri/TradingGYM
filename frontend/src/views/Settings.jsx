import { useT } from "../i18n";
import ConnectorKeys from "../components/ConnectorKeys";
import Icon from "../components/Icon";
import { money, request } from "../services";
import { useState, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
export default function Settings({ data, act, pending }) {
  const t = useT();
  const query = useQuery({
    queryKey: ["agent-settings"],
    queryFn: () => request("/settings"),
  });
  const [draft, setDraft] = useState(null),
    [saved, setSaved] = useState(false),
    [error, setError] = useState("");
  useEffect(() => {
    if (query.data)
      setDraft((current) =>
        current
          ? { ...current, broker_configured: query.data.broker_configured }
          : { ...query.data, symbols: query.data.symbols.join(", ") },
      );
  }, [query.data]);
  const mutation = useMutation({
    mutationFn: (body) =>
      request("/settings", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      setSaved(true);
      query.refetch();
    },
    onError: (e) => setError(e.message),
  });
  function change(key, value) {
    setDraft({
      ...draft,
      [key]: value,
    });
    setSaved(false);
    setError("");
  }
  async function submit(e) {
    e.preventDefault();
    const { broker_configured, llm_configured, live_configured, ...body } =
      draft;
    body.symbols = body.symbols
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    mutation.mutate(body);
  }
  return (
    <>
      <div className="page-title">
        <div>
          <p className="eyebrow">{t("BROKER & RISK POLICY")}</p>
          <h1>{t("Agent settings")}</h1>
          <p>{t("Set the boundaries. The agent operates inside them.")}</p>
        </div>
        <span className="pill">{t("LOCAL CONFIGURATION")}</span>
      </div>
      {!draft ? (
        <div className="surface loading">
          {query.isError
            ? t("Settings could not be loaded.")
            : t("Loading settings…")}
        </div>
      ) : (
        <form onSubmit={submit}>
          <section className="surface settings-surface">
            <div className="settings-intro">
              <span className="workflow-icon">
                <Icon name="layers" />
              </span>
              <h2>{t("Market universe")}</h2>
              <p>
                {t(
                  "USDT spot pairs share one virtual account. Changing the universe requires a flat, stopped account.",
                )}
              </p>
            </div>
            <div className="settings-fields">
              <label>
                {t("Symbols")}
                <input
                  aria-label={t("Symbols")}
                  value={draft.symbols}
                  onChange={(e) => change("symbols", e.target.value)}
                  required
                />
                <small>
                  {t("Comma-separated pairs, for example BTCUSDT, ETHUSDT.")}
                </small>
              </label>
              <label>
                {t("Opening paper capital")}
                <input value={money(draft.initial_cash)} disabled />
                <small>{t("Fixed when the account is created.")}</small>
              </label>
            </div>
          </section>
          <section className="surface settings-surface">
            <div className="settings-intro">
              <span className="workflow-icon">
                <Icon name="shield" />
              </span>
              <h2>{t("Financial risk limits")}</h2>
              <p>
                {t(
                  "These limits remain authoritative over every signal and planner decision.",
                )}
              </p>
            </div>
            <div className="settings-fields risk-fields">
              {[
                ["max_drawdown", t("Maximum drawdown"), 100],
                ["max_daily_loss", t("Maximum daily loss"), 100],
                ["max_position_fraction", t("Per-market exposure cap"), 100],
                ["stop_loss_fraction", t("Stop-loss distance"), 100],
                ["fee_bps", t("Fee assumption (bps)"), 1],
                ["slippage_bps", t("Slippage assumption (bps)"), 1],
              ].map(([key, label, scale]) => (
                <label key={key}>
                  {label}
                  {scale === 100 ? " (%)" : ""}
                  <input
                    type="number"
                    aria-label={label}
                    min={scale === 100 ? 0.01 : 0}
                    max={
                      scale === 100 && key !== "max_position_fraction"
                        ? 50
                        : 100
                    }
                    step="0.01"
                    required
                    value={Number((draft[key] * scale).toFixed(4))}
                    onChange={(e) =>
                      change(key, Number(e.target.value) / scale)
                    }
                  />
                </label>
              ))}
            </div>
          </section>
          <section className="surface settings-surface">
            <div className="settings-intro">
              <span className="workflow-icon">
                <Icon name="key" />
              </span>
              <h2>{t("Broker connection")}</h2>
              <p>
                {t(
                  "Broker credentials stay on the local server and are never sent to the browser.",
                )}
              </p>
            </div>
            <div className="settings-fields">
              <div className="connection-setting">
                <span>{t("Abantether market data")}</span>
                <span className={`pill ${draft.broker_configured ? "ok" : ""}`}>
                  {draft.broker_configured
                    ? t("Configured")
                    : t("Not configured")}
                </span>
              </div>
              <p className="fine-print">
                {t("Set")}
                <code>{t("ABAN_API_KEY")}</code>
                {t(
                  "in the server environment, then restart. You can also save the key securely below.",
                )}
              </p>
              <div className="connection-setting">
                <span>{t("Live execution")}</span>
                <span className="pill">{t("Unavailable")}</span>
              </div>
              <p className="fine-print">
                {t(
                  "No live account adapter is configured. Starting the paper agent cannot submit real orders.",
                )}
              </p>
            </div>
          </section>
          <section className="surface settings-surface">
            <div className="settings-intro">
              <span className="workflow-icon">
                <Icon name="cpu" />
              </span>
              <h2>{t("Planning & learning")}</h2>
              <p>
                {t(
                  "Model advice is bounded by your risk policy. Learning uses broker history and can promote only into paper decisions.",
                )}
              </p>
            </div>
            <div className="settings-fields">
              <label>
                {t("Robot operating mode")}
                <select
                  value={draft.execution_mode || "observe"}
                  onChange={(e) => change("execution_mode", e.target.value)}
                >
                  <option value="observe">{t("Observe real markets")}</option>
                  <option value="paper">
                    {t("Paper execution on real markets")}
                  </option>
                </select>
              </label>
              <label>
                {t("Decision-1 head")}
                <select
                  value={draft.decision1_enabled ? "on" : "off"}
                  onChange={(e) =>
                    change("decision1_enabled", e.target.value === "on")
                  }
                >
                  <option value="off">{t("Disabled")}</option>
                  <option value="on">{t("Enabled")}</option>
                </select>
              </label>
              {draft.decision1_enabled && (
                <>
                  <label>
                    {t("Decision-1 endpoint")}
                    <input
                      type="url"
                      required
                      value={draft.decision1_base_url}
                      onChange={(e) =>
                        change("decision1_base_url", e.target.value)
                      }
                    />
                  </label>
                  <label>
                    {t("Decision-1 model")}
                    <input
                      required
                      value={draft.decision1_model}
                      onChange={(e) =>
                        change("decision1_model", e.target.value)
                      }
                    />
                  </label>
                  <p className="fine-print">
                    {t(
                      "OpenAI-compatible chat gateway with structured decision output. Keys are managed below.",
                    )}
                  </p>
                </>
              )}
              <label>
                {t("Strategy planner")}
                <select
                  value={draft.llm_enabled ? "model" : "local"}
                  onChange={(e) =>
                    change("llm_enabled", e.target.value === "model")
                  }
                >
                  <option value="local">
                    {t("Local deterministic planner")}
                  </option>
                  <option value="model">{t("OpenAI-compatible model")}</option>
                </select>
              </label>
              {draft.llm_enabled && (
                <>
                  <label>
                    {t("Model endpoint")}
                    <input
                      type="url"
                      required
                      value={draft.llm_base_url}
                      onChange={(e) => change("llm_base_url", e.target.value)}
                    />
                  </label>
                  <label>
                    {t("Model name")}
                    <input
                      required
                      value={draft.llm_model}
                      onChange={(e) => change("llm_model", e.target.value)}
                    />
                  </label>
                  <p className="fine-print">
                    {t(
                      "Optional provider credentials come from LLM_API_KEY on the server. A failed planner reduces exposure to half the configured cap.",
                    )}
                  </p>
                </>
              )}
              <label>
                {t("Shadow learning")}
                <select
                  value={draft.shadow_learning_enabled ? "on" : "off"}
                  onChange={(e) =>
                    change("shadow_learning_enabled", e.target.value === "on")
                  }
                >
                  <option value="on">
                    {t("Train and compare paper challengers")}
                  </option>
                  <option value="off">{t("Disabled")}</option>
                </select>
              </label>
              <p className="fine-print">
                {t("Requires")}
                {draft.shadow_minimum_bars}
                {t(
                  "closed broker bars. Each trial keeps the final 30% out of training and checks return, drawdown, and adjusted Sharpe evidence. Only observed broker bars are used.",
                )}
              </p>
            </div>
          </section>
          <div className="settings-save">
            {error && (
              <p className="negative" role="alert">
                {error}
              </p>
            )}
            {saved && (
              <p className="positive" role="status">
                {t("Settings saved.")}
              </p>
            )}
            <span className="muted">
              {t("Stop the agent before applying changes.")}
            </span>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={mutation.isPending || data.status === "running"}
            >
              {mutation.isPending ? t("Saving…") : t("Save settings")}
            </button>
          </div>
        </form>
      )}
      <ConnectorKeys />
      {data.halted && (
        <section className="surface reset-surface">
          <div>
            <h2>{t("Review and reset risk halt")}</h2>
            <p>
              {t(
                "Available only after the agent is stopped and all paper positions are closed. This resets the risk reference to current equity.",
              )}
            </p>
          </div>
          <button
            className="btn"
            disabled={
              pending || data.status === "running" || data.positions.length > 0
            }
            onClick={() => act("reset-halt")}
          >
            {t("Reset halt")}
          </button>
        </section>
      )}
    </>
  );
}
