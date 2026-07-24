import { useEffect, useState } from "react";

export type ThemePref = "dark" | "light" | "system";
const KEY = "agenttic_theme";

/** Persisted appearance preference; dark is the default (Chronometer obsidian). */
export function getThemePref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "system" ? v : "dark";
  } catch {
    return "dark";
  }
}

export function systemTheme(): "dark" | "light" {
  return typeof matchMedia !== "undefined" &&
    matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function resolveTheme(pref: ThemePref): "dark" | "light" {
  return pref === "system" ? systemTheme() : pref;
}

/** Apply (set <html data-theme>) and persist the preference. */
export function applyThemePref(pref: ThemePref) {
  document.documentElement.setAttribute("data-theme", resolveTheme(pref));
  try { localStorage.setItem(KEY, pref); } catch { /* ignore */ }
}

/** Hook for the single Appearance control in Settings: returns the saved
 *  preference and a setter that applies app-wide. Follows the OS when set to
 *  "system". */
export function useThemePref(): [ThemePref, (p: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(getThemePref);
  useEffect(() => { applyThemePref(pref); }, [pref]);
  useEffect(() => {
    if (pref !== "system" || typeof matchMedia === "undefined") return;
    const mq = matchMedia("(prefers-color-scheme: light)");
    const on = () => applyThemePref("system");
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, [pref]);
  return [pref, setPref];
}
