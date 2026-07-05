import type { ButtonHTMLAttributes, ReactNode } from "react";

import type { Intent, StatusIntent } from "./intent";

type ButtonVariant = "primary" | "secondary" | "tertiary" | "destructive";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  small?: boolean;
  block?: boolean;
  loading?: boolean;
  iconOnly?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "primary",
  small,
  block,
  loading,
  iconOnly,
  className,
  children,
  disabled,
  type,
  ...rest
}: Readonly<ButtonProps>) {
  const classes = ["ds-btn", `ds-btn--${variant}`];
  if (small) classes.push("ds-btn--sm");
  if (block) classes.push("ds-btn--block");
  if (loading) classes.push("ds-btn--loading");
  if (iconOnly) classes.push("ds-btn--icon");
  if (className) classes.push(className);
  return (
    <button
      type={type ?? "button"}
      className={classes.join(" ")}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <span className="ds-spinner" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export function Badge({ intent, solid, children }: Readonly<{ intent: Intent; solid?: boolean; children: ReactNode }>) {
  const classes = ["ds-badge", `ds-badge--${intent}`];
  if (solid) classes.push("ds-badge--solid");
  return <span className={classes.join(" ")}>{children}</span>;
}

export function StatusPill({ intent, children }: Readonly<{ intent: StatusIntent; children: ReactNode }>) {
  return (
    <span className={`ds-status ds-status--${intent}`}>
      <span className="ds-status__dot" /> {children}
    </span>
  );
}

export function Alert({
  intent,
  title,
  role = "status",
  children,
}: Readonly<{
  intent: Intent;
  title?: string;
  role?: "status" | "alert";
  children?: ReactNode;
}>) {
  return (
    <div className={`ds-alert ds-alert--${intent}`} role={role}>
      <div>
        {title ? <p className="ds-alert__title">{title}</p> : null}
        {children ? <p className="ds-alert__body">{children}</p> : null}
      </div>
    </div>
  );
}

export function Spinner({ label = "Loading" }: Readonly<{ label?: string }>) {
  // <output> carries an implicit role="status" (a11y) without the explicit role attribute.
  return <output className="ds-spinner" aria-label={label} />;
}

export function Skeleton({ width }: Readonly<{ width?: string }>) {
  return <div className="ds-skeleton" style={width ? { inlineSize: width } : undefined} aria-hidden="true" />;
}

export function EmptyState({
  title,
  children,
  action,
}: Readonly<{ title: string; children?: ReactNode; action?: ReactNode }>) {
  return (
    <div className="ds-empty">
      <p className="ds-empty__title">{title}</p>
      {children ? <p style={{ margin: 0 }}>{children}</p> : null}
      {action}
    </div>
  );
}
