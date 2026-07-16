/**
 * CTF participant workspace client-route paths (#1372).
 *
 * The CTF participant workspace still has a live legacy Django app at these same
 * paths (`ctf/urls.py`); every path builder here matches its page paths exactly,
 * trailing slash included, so a client-router deep link or refresh resolves the
 * same page the Django host would have served (the backend serves the SPA shell
 * for the wrapped participant page paths, and a scoped catch-all covers deeper
 * client sub-routes, once both SPA flags are on). Participant and organizer share
 * the `/ctf/` prefix, so these paths are not free to change shape independently
 * of the backend. Keeping the prefix in one place means a future re-mount changes
 * one file.
 */
export const CTF_BASE = "/ctf";

export const ctfEventHomePath = (): string => `${CTF_BASE}/`;
export const ctfEventPath = (): string => `${CTF_BASE}/event/`;
export const ctfChallengesPath = (): string => `${CTF_BASE}/challenges/`;
export const ctfChallengeDetailPath = (challengeId: string): string => `${CTF_BASE}/challenges/${challengeId}/`;
export const ctfRangePath = (): string => `${CTF_BASE}/range/`;
export const ctfScoreboardPath = (): string => `${CTF_BASE}/scoreboard/`;
export const ctfTeamPath = (): string => `${CTF_BASE}/team/`;
export const ctfHelpPath = (): string => `${CTF_BASE}/help/`;
