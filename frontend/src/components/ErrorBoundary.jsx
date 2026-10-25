import { translate as t } from "../i18n";
import React from "react";
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
    };
  }
  static getDerivedStateFromError() {
    return {
      hasError: true,
    };
  }
  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="connection-state" role="alert">
          <div>
            <h1>{t("The cockpit could not be displayed.")}</h1>
            <p>
              {t(
                "Your agent runs independently. Reload to reconnect and check its status.",
              )}
            </p>

            <div className="error-actions">
              <button
                onClick={() => window.location.reload()}
                className="btn btn-primary"
              >
                {t("Reload Page")}
              </button>
              <button
                onClick={() =>
                  this.setState({
                    hasError: false,
                  })
                }
                className="btn btn-secondary"
              >
                {t("Try Again")}
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
export default ErrorBoundary;
