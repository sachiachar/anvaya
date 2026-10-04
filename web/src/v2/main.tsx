// The new design of the customer app (v2), served at /v2.html beside the classic one.
// It shares the API client, sign-in, live call and vault crypto with the classic app,
// and has its own styles: it never loads ../styles.css.
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
