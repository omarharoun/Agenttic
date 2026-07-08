import { ViteReactSSG } from "vite-react-ssg";
import { routes } from "./App";
import "./theme.css";

/* vite-react-ssg owns the router: it prerenders the public routes to static
   HTML at build time and hydrates the same tree on the client. The heavy
   /app/* console stays a client-only chunk (see App.tsx / vite.config.ts). */
export const createRoot = ViteReactSSG({ routes });
