import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

/**
 * Platform Settings is a read-only informational surface (#1373). Platform
 * configuration (environment, identity provider, cloud, Kubernetes, Terraform,
 * secrets, rollout flags) is owned by deployment; there is no validated platform
 * settings mutation service, so this workspace explains that authority and never
 * edits it. Personal account settings are a separate product surface rather than
 * part of this deployment-owned configuration view.
 */
export function PlatformSettingsPage() {
  return (
    <>
      <PageHeader title="Platform settings" description="Read-only platform configuration" />

      <Alert className="mb-4">
        <AlertTitle>Managed by deployment</AlertTitle>
        <AlertDescription>
          Platform configuration is owned by the deployment pipeline and is not editable here. The platform SPA and RAES
          provisioning path are the current product authorities. Personal account settings are not part of this platform
          configuration surface.
        </AlertDescription>
      </Alert>
    </>
  );
}
