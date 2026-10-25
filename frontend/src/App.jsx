import { useT, useLanguage, LanguageProvider } from "./i18n";
import { useEffect, useRef, useState } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import ErrorBoundary from "./components/ErrorBoundary";
import Icon, { Brand } from "./components/Icon";
import "./App.css";
const client = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
});
import { request } from "./services";
import Monitor from "./views/Monitor";
import Experience from "./views/Experience";
import Settings from "./views/Settings";
function Cockpit() {
  const t = useT();
  const { language, setLanguage } = useLanguage();
  const queries = useQueryClient();
  const modalRef = useRef(null);
  const [view, setView] = useState("monitor"),
    [error, setError] = useState(""),
    [flattenOpen, setFlattenOpen] = useState(false);
  useEffect(() => {
    if (!flattenOpen) return;
    const previous = document.activeElement;
    const modal = modalRef.current;
    const buttons = () => [...modal.querySelectorAll("button:not(:disabled)")];
    buttons()[0]?.focus();
    function keydown(event) {
      if (event.key === "Escape") setFlattenOpen(false);
      if (event.key === "Tab") {
        const items = buttons(),
          first = items[0],
          last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    }
    modal.addEventListener("keydown", keydown);
    return () => {
      modal.removeEventListener("keydown", keydown);
      previous?.focus();
    };
  }, [flattenOpen]);
  const query = useQuery({
    queryKey: ["agent"],
    queryFn: () => request(""),
    refetchInterval: 1500,
  });
  const mutation = useMutation({
    mutationFn: ({ path, body }) =>
      request("/" + path, {
        method: "POST",
        ...(body
          ? {
              body: JSON.stringify(body),
            }
          : {}),
      }),
    onSuccess: (d) => {
      queries.setQueryData(["agent"], d);
      queries.invalidateQueries({
        queryKey: ["agent"],
      });
      setError("");
      setFlattenOpen(false);
    },
    onError: (e) => {
      setError(
        e.message +
          " Refresh account status before repeating an uncertain action.",
      );
      queries.invalidateQueries({
        queryKey: ["agent"],
      });
    },
  });
  const act = (path, body) =>
    mutation.mutate({
      path,
      body,
    });
  const data = query.data;
  const live = data?.status === "running";
  return (
    <div className="cockpit">
      <a className="skip-link" href="#main-content">
        {t("Skip to main content")}
      </a>
      <aside className="cockpit-rail">
        <Brand />
        <span className="rail-caption">{t("MISSION CONTROL")}</span>
        <nav aria-label={t("Main navigation")}>
          {[
            ["monitor", t("Live monitor"), "activity"],
            ["experience", t("Experience"), "layers"],
            ["settings", t("Settings"), "settings"],
          ].map(([id, label, icon]) => (
            <button
              key={id}
              aria-label={label}
              aria-current={view === id ? "page" : undefined}
              className={view === id ? "active" : ""}
              onClick={() => setView(id)}
            >
              <Icon name={icon} size={19} />
              <span>{label}</span>
              {view === id && <span className="nav-active-dot" />}
            </button>
          ))}
        </nav>
        <div className="rail-bottom">
          <div className="paper-mark">
            <Icon name="shield" size={16} />
            <div>
              <strong>{t("Abantether connector")}</strong>
              <small>{t("Market & account records")}</small>
            </div>
          </div>
          <span className="rail-version">{t("TRADING GYM / 32")}</span>
        </div>
      </aside>
      <div className="cockpit-body">
        <header className="command-bar">
          <label className="language-picker">
            <span className="sr-only">{t("Language")}</span>
            <select
              aria-label={t("Language")}
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
            >
              <option value="en">English</option>
              <option value="fa">فارسی</option>
            </select>
          </label>
          <div className="system-state">
            <span
              className={`status-dot ${live && !data.paused ? "" : "idle"}`}
            />
            <strong>
              {query.isError
                ? t("OFFLINE")
                : live
                  ? data.paused
                    ? t("PAUSED")
                    : t("AGENT RUNNING")
                  : data?.status === "halted"
                    ? t("RISK HALTED")
                    : t("AGENT STANDBY")}
            </strong>
            <span className="command-divider" />
            <span className="command-mode">
              {data?.mode === "observe" ? t("OBSERVE") : t("PAPER")}
            </span>
          </div>
          <div className="command-meta">
            <Icon name="clock" size={14} />
            <span>
              {data?.heartbeat
                ? "Heartbeat " +
                  new Date(data.heartbeat * 1000).toLocaleTimeString()
                : t("Awaiting first heartbeat")}
            </span>
          </div>
          <button
            className="btn btn-emergency"
            disabled={!data || mutation.isPending}
            onClick={() => setFlattenOpen(true)}
          >
            <Icon name="shield" size={15} />
            {t("Flatten paper")}
          </button>
        </header>
        <main id="main-content" tabIndex={-1}>
          {error && (
            <div className="alert" role="alert">
              {error}
              <button
                className="icon-button"
                aria-label="Dismiss error"
                onClick={() => setError("")}
              >
                <Icon name="close" size={15} />
              </button>
            </div>
          )}
          {query.isError ? (
            <div className="offline-state">
              <Icon name="activity" size={40} />
              <h1>{t("Backend is not connected.")}</h1>
              <p>
                {t(
                  "The cockpit is ready. Start the local server to load your agent and account.",
                )}
              </p>
              <button
                className="btn btn-primary"
                onClick={() => query.refetch()}
              >
                {t("Retry connection")}
              </button>
            </div>
          ) : !data ? (
            <div className="offline-state">
              <div className="loading-ring" />
              <h1>{t("Opening your workspace…")}</h1>
              <p>{t("Reading local account state.")}</p>
            </div>
          ) : view === "monitor" ? (
            <Monitor
              data={data}
              act={act}
              pending={mutation.isPending}
              onExperience={() => setView("experience")}
            />
          ) : view === "experience" ? (
            <Experience data={data} />
          ) : (
            <Settings data={data} act={act} pending={mutation.isPending} />
          )}
          <footer className="cockpit-footer">
            <span>
              <span className="status-dot" />
              {t("LOCAL WORKSPACE")}
            </span>
            <span>{t("Deterministic limits. Observable decisions.")}</span>
            <span>{t("TRADING GYM")}</span>
          </footer>
        </main>
      </div>
      {flattenOpen && (
        <div className="modal-overlay" onClick={() => setFlattenOpen(false)}>
          <section
            className="confirm-modal"
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="flatten-title"
            onClick={(e) => e.stopPropagation()}
          >
            <span className="confirm-icon">
              <Icon name="shield" size={28} />
            </span>
            <h2 id="flatten-title">{t("Flatten the paper account?")}</h2>
            <p>
              {t(
                "This closes simulated positions at their latest observed marks, applies modeled costs, and latches the risk halt. No broker orders are submitted.",
              )}
            </p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setFlattenOpen(false)}>
                {t("Keep running")}
              </button>
              <button
                className="btn btn-emergency"
                disabled={mutation.isPending}
                onClick={() => act("flatten")}
              >
                {mutation.isPending ? t("Flattening…") : t("Flatten & halt")}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
export default function App() {
  return (
    <LanguageProvider>
      <QueryClientProvider client={client}>
        <ErrorBoundary>
          <Cockpit />
        </ErrorBoundary>
      </QueryClientProvider>
    </LanguageProvider>
  );
}
