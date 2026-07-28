/**
 * RAES image registry client-route paths (#1566).
 *
 * Greenfield SPA-only surface: there is no legacy Django page at this path. The
 * Django host serves the SPA shell for `/raes-image-registry/*` GET paths only
 * when `PLATFORM_SPA_ENABLED` and `SHIFTER_RAES_NATIVE_PROVISIONING` are both on
 * (see `config/urls.py`), so a client-router deep link or refresh resolves the
 * same page. Keeping the prefix in one place means a future re-mount changes one
 * file.
 */
export const RAES_IMAGE_REGISTRY_BASE = "/raes-image-registry";

export const raesImageRegistryPath = (): string => `${RAES_IMAGE_REGISTRY_BASE}/`;
