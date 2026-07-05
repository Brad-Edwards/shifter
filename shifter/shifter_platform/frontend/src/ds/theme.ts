/** Light/dark theme handling, mirroring the design-system styleguide.js:
 * toggle `data-theme` on <html>; `:root` defaults to dark, and
 * `prefers-color-scheme` applies when no theme is pinned. No inline scripts. */
export type Theme = "light" | "dark";

const ATTR = "data-theme";

export function getPinnedTheme(): Theme | null {
  const value = document.documentElement.getAttribute(ATTR);
  return value === "light" || value === "dark" ? value : null;
}

function prefersLight(): boolean {
  return typeof globalThis.matchMedia === "function" && globalThis.matchMedia("(prefers-color-scheme: light)").matches;
}

export function effectiveTheme(): Theme {
  return getPinnedTheme() ?? (prefersLight() ? "light" : "dark");
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute(ATTR, theme);
}

export function toggleTheme(): Theme {
  const next: Theme = effectiveTheme() === "light" ? "dark" : "light";
  applyTheme(next);
  return next;
}
