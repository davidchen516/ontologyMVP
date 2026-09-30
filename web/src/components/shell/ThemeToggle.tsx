import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

const THEME_KEY = "theme";

type Theme = "light" | "dark";

function currentTheme(): Theme {
  const attr = document.documentElement.dataset.theme;
  return attr === "dark" ? "dark" : "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      /* 隐私模式：不持久化 */
    }
  }, [theme]);

  return (
    <button
      type="button"
      role="switch"
      aria-checked={theme === "dark"}
      aria-label="切换亮色/暗色主题"
      className="rounded-md p-2 hover:bg-surface-muted"
      onClick={() => setTheme((value) => (value === "dark" ? "light" : "dark"))}
    >
      {theme === "dark" ? (
        <Sun aria-hidden className="size-4" />
      ) : (
        <Moon aria-hidden className="size-4" />
      )}
    </button>
  );
}
