// v3: a separate trial of Saathi as a talking avatar (option A). It shares only the API client,
// sign-in and the live call with the app; its screens and styles are its own, and the app doesn't
// link here.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ErrorBoundary from "../ErrorBoundary";
import App from "./App";
import "./v3.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
