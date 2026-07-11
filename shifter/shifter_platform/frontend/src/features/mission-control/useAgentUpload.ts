/**
 * Agent-upload state machine (#1370): initiate -> presigned S3 PUT (with
 * progress) -> complete. Client-side counterpart to the legacy
 * `DirectUploader` (`static/js/upload.js`), matching its three-step flow,
 * field names, and 2048 MB client-side size guard exactly.
 *
 * No step ever auto-retries (ADR-029 / `api/queryClient.ts`): a failure at
 * any step surfaces `error` and returns to `idle`, requiring an explicit new
 * `start()` call from the user.
 */
import { useCallback, useRef, useState } from "react";

import { ApiError } from "@/api/errors";
import { useCancelUpload, useCompleteUpload, useInitiateUpload, type UploadInitiateRequest } from "@/api/mission-control";

import { uploadFileToPresignedUrl, type PresignedUploadHandle } from "./upload";

export type AgentType = NonNullable<UploadInitiateRequest["agent_type"]>;

/** Mirrors `DirectUploader`'s `maxSizeMB` default (`static/js/upload.js`). */
export const MAX_AGENT_UPLOAD_SIZE_MB = 2048;

export type AgentUploadPhase = "idle" | "uploading" | "error";

export interface AgentUploadState {
  phase: AgentUploadPhase;
  progress: number;
  statusText: string;
  error: string | null;
}

export interface StartUploadArgs {
  name: string;
  file: File;
  agentType: AgentType;
}

export interface UseAgentUploadResult extends AgentUploadState {
  start: (args: StartUploadArgs) => void;
  /** Abort the in-flight PUT (if any) and best-effort clean up the upload session server-side. */
  cancel: () => void;
}

const INITIAL_STATE: AgentUploadState = { phase: "idle", progress: 0, statusText: "", error: null };

export function useAgentUpload(): UseAgentUploadResult {
  const initiateUpload = useInitiateUpload();
  const completeUpload = useCompleteUpload();
  const cancelUpload = useCancelUpload();

  const [state, setState] = useState<AgentUploadState>(INITIAL_STATE);
  const inFlightRef = useRef(false);
  const cancelledRef = useRef(false);
  const uploadTokenRef = useRef<string | null>(null);
  const putHandleRef = useRef<PresignedUploadHandle | null>(null);

  const start = useCallback(
    ({ name, file, agentType }: StartUploadArgs) => {
      if (inFlightRef.current) return;

      const trimmedName = name.trim();
      if (!trimmedName) {
        setState({ phase: "error", progress: 0, statusText: "", error: "Agent name is required." });
        return;
      }
      const maxBytes = MAX_AGENT_UPLOAD_SIZE_MB * 1024 * 1024;
      if (file.size > maxBytes) {
        setState({
          phase: "error",
          progress: 0,
          statusText: "",
          error: `File size exceeds the maximum (${MAX_AGENT_UPLOAD_SIZE_MB} MB).`,
        });
        return;
      }

      inFlightRef.current = true;
      cancelledRef.current = false;
      uploadTokenRef.current = null;
      putHandleRef.current = null;
      setState({ phase: "uploading", progress: 0, statusText: "Preparing upload…", error: null });

      void (async () => {
        try {
          const initiated = await initiateUpload.mutateAsync({
            name: trimmedName,
            filename: file.name,
            file_size: file.size,
            agent_type: agentType,
          });
          if (cancelledRef.current) return;
          uploadTokenRef.current = initiated.upload_token;

          setState((prev) => ({ ...prev, statusText: "Uploading…" }));
          const handle = uploadFileToPresignedUrl(initiated.presigned_url, file, (percent) => {
            setState((prev) => ({ ...prev, progress: percent, statusText: `Uploading… ${percent}%` }));
          });
          putHandleRef.current = handle;
          await handle.promise;
          if (cancelledRef.current) return;

          setState((prev) => ({ ...prev, statusText: "Finalizing…" }));
          await completeUpload.mutateAsync({ upload_token: initiated.upload_token });
          if (cancelledRef.current) return;

          setState(INITIAL_STATE);
        } catch (err) {
          if (cancelledRef.current) return;
          const message =
            err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Upload failed.";
          setState({ phase: "error", progress: 0, statusText: "", error: message });
          // Best-effort server-side cleanup of the upload-in-progress session
          // marker; a cleanup failure never overrides the error already shown.
          if (uploadTokenRef.current) {
            cancelUpload.mutate({ upload_token: uploadTokenRef.current });
          }
        } finally {
          inFlightRef.current = false;
          putHandleRef.current = null;
        }
      })();
    },
    [initiateUpload, completeUpload, cancelUpload],
  );

  const cancel = useCallback(() => {
    if (!inFlightRef.current) return;
    cancelledRef.current = true;
    putHandleRef.current?.abort();
    if (uploadTokenRef.current) {
      cancelUpload.mutate({ upload_token: uploadTokenRef.current });
    }
    inFlightRef.current = false;
    setState(INITIAL_STATE);
  }, [cancelUpload]);

  return { ...state, start, cancel };
}
