import { createRoot } from "react-dom/client";
import { App } from "./App";
import { PipelineLive } from "../pipeline/PipelineLive";
import "./styles.css";

// /pipeline: live signal path for a second device (15 Nov, Sanju)
createRoot(document.getElementById("root")!).render(location.pathname === "/pipeline" ? <PipelineLive /> : <App />);
