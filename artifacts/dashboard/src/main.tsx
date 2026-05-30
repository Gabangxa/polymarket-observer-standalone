import { createRoot } from "react-dom/client";
import { setApiKey } from "@workspace/api-client-react";
import App from "./App";
import "./index.css";

const storedKey = localStorage.getItem("x-api-key");
if (storedKey) setApiKey(storedKey);

createRoot(document.getElementById("root")!).render(<App />);
