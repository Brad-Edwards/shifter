import type { ReactNode } from "react";

import { Alert, Button, Dialog } from "@/ds";
import { ApiError } from "@/api/errors";

/**
 * Confirmation/destructive dialog built on the DS dialog contract. Used for
 * delete, restore, close, reopen, and comment-delete flows (no browser confirm).
 */
export function ConfirmDialog({
  title,
  confirmLabel,
  destructive,
  pending,
  error,
  onConfirm,
  onCancel,
  children,
}: Readonly<{
  title: string;
  confirmLabel: string;
  destructive?: boolean;
  pending?: boolean;
  error?: unknown;
  onConfirm: () => void;
  onCancel: () => void;
  children: ReactNode;
}>) {
  let message: string | null = null;
  if (error instanceof ApiError) {
    message = error.message;
  } else if (error) {
    message = "The action could not be completed.";
  }
  return (
    <Dialog
      title={title}
      onClose={onCancel}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
          <Button variant={destructive ? "destructive" : "primary"} onClick={onConfirm} loading={pending}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div style={{ display: "grid", gap: "var(--ds-space-3)" }}>
        <div>{children}</div>
        {message ? (
          <Alert intent="danger" role="alert">
            {message}
          </Alert>
        ) : null}
      </div>
    </Dialog>
  );
}
