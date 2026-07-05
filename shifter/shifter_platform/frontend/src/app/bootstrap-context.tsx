import { createContext, useContext, type ReactNode } from "react";

import { useBootstrap } from "@/api/bootstrap";
import { ApiError } from "@/api/errors";
import type { Bootstrap } from "@/api/types";
import { Alert, Spinner } from "@/ds";

const BootstrapContext = createContext<Bootstrap | null>(null);

/**
 * Loads the session bootstrap once and provides it to the workspace. On 401
 * (expired session) it redirects to the shared Django login; other failures
 * render a non-leaking error state.
 */
export function BootstrapProvider({ children }: Readonly<{ children: ReactNode }>) {
  const { data, isLoading, error } = useBootstrap();

  if (isLoading) {
    return (
      <div className="ds-empty" role="status" aria-live="polite">
        <Spinner label="Loading workspace" />
      </div>
    );
  }

  if (error || !data) {
    if (error instanceof ApiError && error.status === 401) {
      const next = encodeURIComponent(globalThis.location.pathname + globalThis.location.search);
      globalThis.location.assign(`/login/?next=${next}`);
      return null;
    }
    return (
      <div className="ds-main">
        <Alert intent="danger" role="alert" title="Unable to load the workspace">
          Please retry. If the problem persists, contact an administrator.
        </Alert>
      </div>
    );
  }

  return <BootstrapContext.Provider value={data}>{children}</BootstrapContext.Provider>;
}

export function useBootstrapContext(): Bootstrap {
  const value = useContext(BootstrapContext);
  if (value === null) {
    throw new Error("useBootstrapContext must be used within a BootstrapProvider");
  }
  return value;
}
