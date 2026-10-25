import { useT } from "../i18n";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request, money, num, when } from "../services";
import DecisionTrace from "./DecisionTrace";
export default function RobotHistory() {
  const t = useT();
  const [mode, setMode] = useState("broker"),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState(null);
  const history = useQuery({
    queryKey: ["history", mode, offset],
    queryFn: () =>
      request(
        mode === "decisions"
          ? `/decisions?limit=15&offset=${offset}`
          : `/history?mode=${mode}&limit=15&offset=${offset}`,
      ),
    refetchInterval: 5000,
  });
  const detail = useQuery({
    queryKey: ["decision", selected],
    queryFn: () => request("/decision?id=" + encodeURIComponent(selected)),
    enabled: !!selected,
  });
  const rows = history.data?.items || [];
  return (
    <section className="surface">
      <div className="surface-heading">
        <h2>{t("Robot history & decisions")}</h2>
        <span className="muted small">
          {history.data?.total ?? "—"}
          {t("records")}
        </span>
      </div>
      <div
        className="history-tabs"
        role="group"
        aria-label={t("History account")}
      >
        {[
          ["broker", t("Broker-confirmed fills")],
          ["paper", t("Paper ledger")],
          ["decisions", t("Decision journal")],
        ].map(([id, label]) => (
          <button
            key={id}
            className={`btn ${mode === id ? "active" : ""}`}
            aria-pressed={mode === id}
            onClick={() => {
              setMode(id);
              setOffset(0);
              setSelected(null);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {history.isError ? (
        <p role="alert" className="table-empty">
          {t("History could not be loaded.")}
          <button className="text-button" onClick={() => history.refetch()}>
            {t("Retry")}
          </button>
        </p>
      ) : history.isPending ? (
        <p className="table-empty">{t("Loading history…")}</p>
      ) : !rows.length ? (
        <p className="table-empty">
          {mode === "broker"
            ? t(
                "No confirmed Abantether fills have been received. Connect your account in Settings.",
              )
            : mode === "decisions"
              ? t(
                  "No decisions recorded. The robot needs actual closed market bars.",
                )
              : t("No paper executions recorded in this account.")}
        </p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("Time")}</th>
                <th>{t("Market")}</th>
                <th>{t("Action")}</th>
                {mode !== "decisions" && (
                  <>
                    <th>{t("Quantity")}</th>
                    <th>{t("Price")}</th>
                    <th>{t("Source")}</th>
                  </>
                )}
                <th>{t("Decision route")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{when(row.at)}</td>
                  <td>{row.symbol}</td>
                  <td>{row.side || row.action}</td>
                  {mode !== "decisions" && (
                    <>
                      <td>{num(row.quantity, 8)}</td>
                      <td>{money(row.price)}</td>
                      <td>
                        {row.mode === "broker"
                          ? t("Broker confirmed")
                          : t("Paper ledger")}
                      </td>
                    </>
                  )}
                  <td>
                    {(mode === "decisions" ? row.id : row.decision_id) ? (
                      <button
                        className="text-button"
                        onClick={() =>
                          setSelected(
                            mode === "decisions" ? row.id : row.decision_id,
                          )
                        }
                      >
                        {t("View decision")}
                      </button>
                    ) : (
                      <span className="muted">
                        {t("No recorded robot attribution")}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {history.data?.total > 15 && (
        <div className="history-pagination">
          <button
            className="btn"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 15))}
          >
            {t("Newer")}
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 15, history.data.total)}
          </span>
          <button
            className="btn"
            disabled={offset + 15 >= history.data.total}
            onClick={() => setOffset(offset + 15)}
          >
            {t("Older")}
          </button>
        </div>
      )}
      {selected && (
        <div className="trace-panel">
          <button className="text-button" onClick={() => setSelected(null)}>
            {t("Close details")}
          </button>
          {detail.isPending ? (
            <p>{t("Loading decision…")}</p>
          ) : detail.isError ? (
            <p role="alert">{t("Decision details are unavailable.")}</p>
          ) : (
            <DecisionTrace decision={detail.data} />
          )}
        </div>
      )}
    </section>
  );
}
