import { initializeApp } from "https://www.gstatic.com/firebasejs/12.12.0/firebase-app.js";
import { getAuth, signOut } from "https://www.gstatic.com/firebasejs/12.12.0/firebase-auth.js";

const configScript = document.getElementById("identity-platform-logout-config");

// Only ever navigate to a same-origin path. Parsing through URL and returning
// pathname+search+hash strips any scheme, so a "javascript:" or cross-origin
// value can never reach location.assign (CodeQL js/xss-through-dom).
function safeSameOriginUrl(candidate, fallback = "/") {
    for (const value of [candidate, fallback]) {
        try {
            const url = new URL(value, globalThis.location.origin);
            if (url.origin === globalThis.location.origin) {
                return url.pathname + url.search + url.hash;
            }
        } catch {
            // not parseable / not same-origin; try the next candidate
        }
    }
    return "/";
}

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
        globalThis.location.assign(safeSameOriginUrl(config.redirectUrl));
    }
} else {
    globalThis.location.assign("/");
}
