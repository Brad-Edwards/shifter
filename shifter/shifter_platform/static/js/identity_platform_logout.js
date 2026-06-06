import { initializeApp } from "https://www.gstatic.com/firebasejs/12.12.0/firebase-app.js";
import { getAuth, signOut } from "https://www.gstatic.com/firebasejs/12.12.0/firebase-auth.js";

/**
 * Resolve a redirect target to a same-origin URL string, rejecting
 * anything that could smuggle a dangerous scheme (javascript:, data:,
 * vbscript:) into location.assign. Relative paths and absolute URLs
 * are resolved against the current origin; cross-origin or unparseable
 * targets fall back to the site root. Returns a path-only string so
 * the navigation can never escape the current origin.
 */
function safeRedirectPath(rawUrl, fallback = "/") {
    try {
        const resolved = new URL(String(rawUrl ?? ""), globalThis.location.origin);
        if (resolved.origin !== globalThis.location.origin) {
            return fallback;
        }
        return resolved.pathname + resolved.search + resolved.hash;
    } catch {
        return fallback;
    }
}

const configScript = document.getElementById("identity-platform-logout-config");

if (configScript) {
    const config = JSON.parse(configScript.textContent);

    try {
        const app = initializeApp({
            apiKey: config.apiKey,
            authDomain: config.authDomain,
            projectId: config.projectId,
        });
        await signOut(getAuth(app));
    } catch (error) {
        console.error("Identity Platform logout failed", error);
    } finally {
        globalThis.location.assign(safeRedirectPath(config.redirectUrl, "/"));
    }
} else {
    globalThis.location.assign("/");
}
