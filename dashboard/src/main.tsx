import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { FluentProvider } from "@fluentui/react-components";
import { App } from "./app/App";
import { cyberTheme } from "./theme/cyberTheme";
import "./theme/global.css";
import "./theme/operations.css";
import "./theme/series-rail.css";
import "./theme/bilibili-login.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode><FluentProvider theme={cyberTheme}><App /></FluentProvider></StrictMode>,
);
