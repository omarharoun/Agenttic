import { useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "ascore_theme";

/** Persisted theme; dark is the default (Noor warm-dark). */
export function getTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function applyTheme(t: Theme) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem(KEY, t); } catch { /* ignore */ }
}

/** Small hook + a toggle for components that flip the theme. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(getTheme);
  useEffect(() => { applyTheme(theme); }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}
