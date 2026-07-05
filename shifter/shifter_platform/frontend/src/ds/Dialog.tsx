import { useEffect, useId, useRef, type ReactNode } from "react";

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

interface DialogProps {
  title: string;
  onClose: () => void;
  footer: ReactNode;
  children: ReactNode;
}

/**
 * Modal dialog following the design-system contract: scrim overlay, focus trap,
 * `Esc` to close, focus returned to the trigger on close, labelled title. Used
 * for the destructive/confirm flows (delete, restore, close/reopen, comment
 * delete). No browser `confirm()`.
 */
export function Dialog({ title, onClose, footer, children }: DialogProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    returnFocusRef.current = document.activeElement as HTMLElement | null;
    const node = dialogRef.current;
    const items = () => (node ? Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)) : []);
    items()[0]?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = items();
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      returnFocusRef.current?.focus();
    };
  }, [onClose]);

  return (
    <div className="ds-overlay">
      <div className="ds-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialogRef}>
        <div className="ds-dialog__header">
          <h3 className="ds-dialog__title" id={titleId}>
            {title}
          </h3>
        </div>
        <div className="ds-dialog__body">{children}</div>
        <div className="ds-dialog__footer">{footer}</div>
      </div>
    </div>
  );
}
