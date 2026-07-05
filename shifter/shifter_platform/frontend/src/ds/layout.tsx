import { Fragment, useRef, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Button } from "./primitives";
import { toggleTheme } from "./theme";

export interface NavItem {
  label: string;
  to: string;
  end?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export function AppShell({
  principalName,
  groups,
  currentPath,
  children,
}: {
  principalName: string;
  groups: NavGroup[];
  currentPath: string;
  children: ReactNode;
}) {
  return (
    <>
      <a className="ds-skip-link" href="#main-content">
        Skip to content
      </a>
      <div className="ds-appshell">
        <div className="ds-appshell__brand">Shifter</div>
        <div className="ds-topbar">
          <span className="ds-text-muted">{principalName}</span>
          <span className="ds-topbar__spacer" />
          <Button variant="tertiary" small onClick={() => toggleTheme()}>
            Toggle theme
          </Button>
        </div>
        <nav className="ds-sidenav" aria-label="Primary">
          {groups.map((group) => (
            <Fragment key={group.label}>
              <span className="ds-navgroup__label">{group.label}</span>
              {group.items.map((item) => {
                const active = item.end ? currentPath === item.to : currentPath.startsWith(item.to);
                return (
                  <Link
                    key={item.to}
                    className="ds-navitem"
                    to={item.to}
                    aria-current={active ? "page" : undefined}
                  >
                    {item.label}
                  </Link>
                );
              })}
            </Fragment>
          ))}
        </nav>
        <main className="ds-main" id="main-content">
          {children}
        </main>
      </div>
    </>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="ds-page-header">
      <div>
        <h1 className="ds-page-title">{title}</h1>
        {subtitle ? (
          <p className="ds-text-muted" style={{ margin: "var(--ds-space-1) 0 0" }}>
            {subtitle}
          </p>
        ) : null}
      </div>
      {actions ? <div className="ds-page-header__actions">{actions}</div> : null}
    </div>
  );
}

export interface Crumb {
  label: string;
  to?: string;
}

export function Breadcrumb({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb">
      <ol className="ds-breadcrumb">
        {items.map((item, index) => (
          <Fragment key={`${item.label}-${index}`}>
            {index > 0 ? (
              <li className="ds-breadcrumb__sep" aria-hidden="true">
                /
              </li>
            ) : null}
            <li>
              {item.to ? (
                <Link className="ds-link" to={item.to}>
                  {item.label}
                </Link>
              ) : (
                <span className="ds-breadcrumb__current" aria-current="page">
                  {item.label}
                </span>
              )}
            </li>
          </Fragment>
        ))}
      </ol>
    </nav>
  );
}

export interface TabItem {
  id: string;
  label: string;
}

export function Tabs({
  items,
  value,
  onChange,
  label,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}) {
  const ref = useRef<HTMLDivElement>(null);

  // Roving-tabindex keyboard handling lives on the focusable tab buttons (APG
  // authoring practice); the tablist container itself is not focusable.
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const index = items.findIndex((item) => item.id === value);
    let next = index;
    switch (event.key) {
      case "ArrowRight":
        next = (index + 1) % items.length;
        break;
      case "ArrowLeft":
        next = (index - 1 + items.length) % items.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = items.length - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    onChange(items[next].id);
    const buttons = ref.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    buttons?.[next]?.focus();
  }

  return (
    <div className="ds-tabs" role="tablist" aria-label={label} ref={ref}>
      {items.map((item) => (
        <button
          key={item.id}
          className="ds-tab"
          role="tab"
          type="button"
          id={`tab-${item.id}`}
          aria-controls={`panel-${item.id}`}
          aria-selected={item.id === value}
          tabIndex={item.id === value ? 0 : -1}
          onClick={() => onChange(item.id)}
          onKeyDown={onKeyDown}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
