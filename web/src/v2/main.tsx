// The customer app. Its screens and styles live in src/v2/; the API client, sign-in, live call
// and vault crypto are in src/, shared with the platform admin portal where they apply.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ErrorBoundary from "../ErrorBoundary";
import App from "./App";
import "./v2.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
