import { useT } from "../i18n";
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { request, num } from "../services";
export default function ConnectorKeys() {
  const t = useT();
  const queryClient = useQueryClient();
  const check = useMutation({
    mutationFn: () => request("/broker/refresh", { method: "POST" }),
    onSuccess: (result) => {
      setMessage(
        result.status === "connected" ? "Abantether connected" : result.error,
      );
      queryClient.invalidateQueries({ queryKey: ["agent"] });
    },
    onError: () =>
      setMessage(
        "Abantether connection unavailable. Check the API key, network, and broker response contract.",
      ),
  });
  const query = useQuery({
    queryKey: ["credentials"],
    queryFn: () => request("/credentials"),
  });
  const [broker, setBroker] = useState(""),
    [llm, setLlm] = useState([""]),
    [decision1, setDecision1] = useState([""]),
    [message, setMessage] = useState("");
  const mutation = useMutation({
    mutationFn: (body) =>
      request("/credentials", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      setBroker("");
      setLlm([""]);
      setDecision1([""]);
      setMessage("API keys saved. Values are never returned to the browser.");
      query.refetch();
      queryClient.invalidateQueries({ queryKey: ["agent-settings"] });
      queryClient.invalidateQueries({ queryKey: ["agent"] });
    },
    onError: () =>
      setMessage(
        "Keys could not be saved. Check for duplicate or invalid values.",
      ),
  });
  function save(event) {
    event.preventDefault();
    const body = {};
    if (broker.trim()) body.abantether = broker.trim();
    if (llm.some((k) => k.trim()))
      body.llm = llm.map((k) => k.trim()).filter(Boolean);
    if (decision1.some((k) => k.trim()))
      body.decision1 = decision1.map((k) => k.trim()).filter(Boolean);
    if (Object.keys(body).length) mutation.mutate(body);
  }
  function roster(role, keys, setKeys) {
    return (
      <div className="key-roster">
        <h3>
          {role === "llm"
            ? t("LLM planner keys")
            : t("Decision-1 gateway keys")}
        </h3>
        <p>
        {num(query.data?.[role]?.length ?? 0,0)}
          {t("saved keys")}
        </p>
        {keys.map((key, i) => (
          <div className="key-row" key={i}>
            <input
              type="password"
              autoComplete="new-password"
              aria-label={`${role} API key ${i + 1}`}
              placeholder={`API key ${i + 1}`}
              value={key}
              onChange={(e) =>
                setKeys(keys.map((k, j) => (j === i ? e.target.value : k)))
              }
            />
            <button
              type="button"
              className="text-button"
              aria-label={`Remove ${role} key ${i + 1}`}
              onClick={() => setKeys(keys.filter((_, j) => j !== i))}
            >
              {t("Remove")}
            </button>
          </div>
        ))}
        <button
          type="button"
          className="text-button"
          disabled={keys.length >= 16}
          onClick={() => setKeys([...keys, ""])}
        >
          {t("Add API key")}
        </button>
        {!!query.data?.[role]?.length && (
          <button
            type="button"
            className="text-button"
            disabled={mutation.isPending}
            onClick={() =>
              mutation.mutate({
                [role]: [],
              })
            }
          >
            {t("Remove saved keys")}
          </button>
        )}
      </div>
    );
  }
  return (
    <section className="surface settings-surface">
      <div className="settings-intro">
        <h2>{t("API keys & rotation")}</h2>
        <p>
          {t(
            "Secrets are encrypted on this computer. Blank fields keep saved keys. A new key list replaces the saved list for that gateway.",
          )}
        </p>
        <p>
          {t(
            "Keys rotate across requests. Failed keys cool down; provider-wide limits apply to the whole pool.",
          )}
        </p>
      </div>
      <form className="settings-fields" onSubmit={save}>
        <label>
          {t("Abantether API key")}
          <input
            type="password"
            autoComplete="new-password"
            value={broker}
            onChange={(e) => setBroker(e.target.value)}
            placeholder={
              query.data?.abantether_configured
                ? t("A key is saved")
                : t("Enter your Abantether API key")
            }
          />
        </label>
        <p className="fine-print">
          {t(query.data?.storage || "Loading credential storage…")}
        </p>
        {roster("llm", llm, setLlm)}
        {roster("decision1", decision1, setDecision1)}
        <button
          className="btn btn-primary"
          disabled={mutation.isPending}
          type="submit"
        >
          {mutation.isPending ? t("Saving…") : t("Save API keys")}
        </button>
        {message && <p role="status">{t(message)}</p>}
        <button
          type="button"
          className="btn"
          disabled={check.isPending}
          onClick={() => check.mutate()}
        >
          {check.isPending
            ? t("Checking connection…")
            : t("Refresh Abantether")}
        </button>
      </form>
    </section>
  );
}
