import { useTheme } from "../theme";

/** Round icon button that flips between the warm-dark (default) and warm-cream
 *  themes. Choice is persisted; the no-flash boot script in index.html applies
 *  it before first paint. */
export function ThemeToggle({ className = "icon-btn" }: { className?: string }) {
  const [theme, toggle] = useTheme();
  const dark = theme === "dark";
  return (
    <button
      className={className}
      onClick={toggle}
      title={dark ? "Switch to light" : "Switch to dark"}
      aria-label="Toggle theme"
    >
      {dark ? "☀" : "☾"}
    </button>
  );
}
