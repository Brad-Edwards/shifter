/**
 * TanStack Query hooks for the CTF participant workspace API surface (#1372).
 * All caching, invalidation, and retry policy live here; components call these
 * hooks and never fetch directly. The SPA uses the canonical `/api/v1/ctf/` DRF
 * routes only — it never touches the legacy `/ctf/` Django page/form URLs.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "./client";
import type {
  CtfChallengeDetail,
  CtfChallengeListItem,
  CtfCurrentEvent,
  CtfRangeAccess,
  CtfRangeStatus,
  CtfScoreboard,
  CtfSubmissionList,
  CtfSubmitFlagResult,
  CtfTeam,
  CtfUseHintResult,
} from "./types";

const BASE = "/ctf";

export const ctfKeys = {
  all: ["ctf"] as const,
  currentEvent: () => ["ctf", "current-event"] as const,
  challenges: () => ["ctf", "challenges"] as const,
  challenge: (id: string) => ["ctf", "challenge", id] as const,
  team: () => ["ctf", "team"] as const,
  submissions: () => ["ctf", "submissions"] as const,
  rangeStatus: () => ["ctf", "range-status"] as const,
  scoreboard: (eventId: string, bracketId?: string) => ["ctf", "scoreboard", eventId, bracketId ?? null] as const,
};

export function useCtfCurrentEvent() {
  return useQuery({
    queryKey: ctfKeys.currentEvent(),
    queryFn: ({ signal }) => apiFetch<CtfCurrentEvent>(`${BASE}/me/event/`, { signal }),
  });
}

export function useCtfChallenges() {
  return useQuery({
    queryKey: ctfKeys.challenges(),
    queryFn: ({ signal }) => apiFetch<CtfChallengeListItem[]>(`${BASE}/me/challenges/`, { signal }),
  });
}

export function useCtfChallenge(challengeId: string, enabled = true) {
  return useQuery({
    queryKey: ctfKeys.challenge(challengeId),
    enabled: enabled && Boolean(challengeId),
    queryFn: ({ signal }) => apiFetch<CtfChallengeDetail>(`${BASE}/me/challenges/${challengeId}/`, { signal }),
  });
}

export function useCtfTeam() {
  return useQuery({
    queryKey: ctfKeys.team(),
    queryFn: ({ signal }) => apiFetch<CtfTeam>(`${BASE}/me/team/`, { signal }),
  });
}

export function useCtfSubmissions() {
  return useQuery({
    queryKey: ctfKeys.submissions(),
    queryFn: ({ signal }) => apiFetch<CtfSubmissionList>(`${BASE}/submissions/`, { signal }),
  });
}

export function useCtfRangeStatus() {
  return useQuery({
    queryKey: ctfKeys.rangeStatus(),
    queryFn: ({ signal }) => apiFetch<CtfRangeStatus>(`${BASE}/range/status/`, { signal }),
  });
}

export function useCtfScoreboard(eventId: string, bracketId?: string, enabled = true) {
  return useQuery({
    queryKey: ctfKeys.scoreboard(eventId, bracketId),
    enabled: enabled && Boolean(eventId),
    queryFn: ({ signal }) =>
      apiFetch<CtfScoreboard>(`${BASE}/events/${eventId}/scoreboard/`, {
        signal,
        query: bracketId ? { bracket: bracketId } : undefined,
      }),
  });
}

/**
 * Invalidate the play-affecting reads after a submission or hint unlock: the
 * challenge detail (attempts/hints/solved), the browse list (solve status), the
 * current-event projection (score/rank), and the submission history.
 */
function invalidatePlay(queryClient: ReturnType<typeof useQueryClient>, challengeId: string) {
  queryClient.invalidateQueries({ queryKey: ctfKeys.challenge(challengeId) });
  queryClient.invalidateQueries({ queryKey: ctfKeys.challenges() });
  queryClient.invalidateQueries({ queryKey: ctfKeys.currentEvent() });
  queryClient.invalidateQueries({ queryKey: ctfKeys.submissions() });
}

export function useSubmitFlag(challengeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (flag: string) =>
      apiFetch<CtfSubmitFlagResult>(`${BASE}/challenges/${challengeId}/submit/`, {
        method: "POST",
        body: { flag },
      }),
    onSuccess: () => invalidatePlay(queryClient, challengeId),
  });
}

export function useUseHint(challengeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    // Omit `hint_id` to unlock the next hint in order, or pass one to unlock a
    // specific hint (mirrors the server's UseHintRequest contract).
    mutationFn: (hintId?: string) =>
      apiFetch<CtfUseHintResult>(`${BASE}/challenges/${challengeId}/hint/`, {
        method: "POST",
        body: hintId ? { hint_id: hintId } : {},
      }),
    onSuccess: () => invalidatePlay(queryClient, challengeId),
  });
}

export function useRangeAccess() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<CtfRangeAccess>(`${BASE}/range/access/`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ctfKeys.rangeStatus() }),
  });
}

/**
 * Resolve a presigned download URL for a challenge attachment (used by the
 * imperative download action on the challenge detail page). The endpoint returns
 * a short-lived URL + filename rather than streaming the file, so the caller
 * fetches this then navigates to `url`.
 */
export function fetchCtfFileDownload(fileId: string): Promise<{ url: string; filename: string }> {
  return apiFetch<{ url: string; filename: string }>(`${BASE}/files/${fileId}/download/`);
}
