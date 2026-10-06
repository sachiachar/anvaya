import { Component, type ErrorInfo, type ReactNode } from "react";

/** A crash on one screen shows a short message with Reload, never a blank page. The details go
 * to the browser console only: they can include data from the screen. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Anvaya screen error", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="crash" role="alert">
        <p><strong>Something went wrong on this screen.</strong></p>
        <p className="crash-note">Your demo is safe. Reloading usually fixes it.</p>
        <button onClick={() => location.reload()}>Reload</button>
      </div>
    );
  }
}
