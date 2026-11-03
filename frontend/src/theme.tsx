import { useEffect, useState } from "react";
import { IconMoon, IconSun } from "./components/icons";

export type Theme = "light" | "dark";
const KEY = "vaultmind.theme";

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

/** Dark until the person picks light, then remembers their choice. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => stored() ?? "dark");

  useEffect(() => apply(theme), [theme]);

  const toggle = () =>
    setTheme((t) => {
      const next = t === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(KEY, next);
      } catch {
        /* storage unavailable: still switch for this session */
      }
      return next;
    });

  return [theme, toggle];
}

export function ThemeToggle({ className = "" }: { className?: string }) {
  const [theme, toggle] = useTheme();
  const label = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  return (
    <button className={`theme-toggle ${className}`} onClick={toggle} aria-label={label} title={label}>
      {theme === "dark" ? <IconSun /> : <IconMoon />}
    </button>
  );
}
