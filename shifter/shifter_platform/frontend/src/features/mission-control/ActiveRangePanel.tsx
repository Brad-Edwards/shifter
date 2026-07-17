import { useState } from "react";

import {
  useCancelRange,
  useDestroyRange,
  usePauseRange,
  useResumeRange,
} from "@/api/mission-control";
import type { RangePresentation, RangeStatus } from "@/api/types";
import { rangeStatusMapping } from "@/app/state-map";
import { PageHeader } from "@/components/page-header";
import { StatusChip } from "@/components/status-chip";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

import { ConfirmDialog } from "@/components/confirm-dialog";
import { InstanceTable } from "./InstanceTable";
import { useRangeStatusSocket } from "./useRangeStatusSocket";

// Statuses that mean the backend is still working; mirrors the set the
// polling hook (api/mission-control.ts) uses to decide whether to keep
// refetching. Duplicated here (rather than imported) because it drives
// presentation (the progress indicator), not caching policy.
const TRANSIENT_STATUSES: ReadonlySet<RangeStatus> = new Set([
  "pending",
  "provisioning",
  "pausing",
  "resuming",
  "destroying",
]);

type LifecycleDialog = "pause" | "resume" | "cancel" | "destroy" | null;

const DIALOG_COPY: Record<
  Exclude<LifecycleDialog, null>,
  { title: string; confirmLabel: string; description: string }
> = {
  pause: {
    title: "Pause range?",
    confirmLabel: "Pause",
    description: "Instances will be stopped but kept. You can resume the range later.",
  },
  resume: {
    title: "Resume range?",
    confirmLabel: "Resume",
    description: "Instances will be started again.",
  },
  cancel: {
    title: "Cancel launch?",
    confirmLabel: "Cancel launch",
    description: "The in-progress range launch will be stopped.",
  },
  destroy: {
    title: "Destroy range?",
    confirmLabel: "Destroy",
    description: "This range and its instances will be permanently destroyed. This cannot be undone.",
  },
};

/**
 * Small, never-color-only live/offline affordance for the range-status socket
 * (advisory only — `useCurrentRange`'s polling `refetchInterval` is the real
 * fallback when the socket is down; see `useRangeStatusSocket`).
 */
function LiveIndicator({ connected }: Readonly<{ connected: boolean }>) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span
        className={cn("size-1.5 rounded-full", connected ? "bg-emerald-500" : "bg-muted-foreground/40")}
        aria-hidden="true"
      />
      {connected ? "Live" : "Offline"}
    </span>
  );
}

function lifecycleActionsFor(range: RangePresentation): Array<{ dialog: LifecycleDialog; label: string }> {
  const actions: Array<{ dialog: LifecycleDialog; label: string }> = [];
  if (range.status === "pending" || range.status === "provisioning") {
    actions.push({ dialog: "cancel", label: "Cancel launch" });
  } else if (range.is_active && range.status !== "destroying") {
    actions.push({ dialog: "destroy", label: "Destroy" });
  }
  if (range.status === "ready") {
    actions.push({ dialog: "pause", label: "Pause" });
  }
  if (range.status === "paused") {
    actions.push({ dialog: "resume", label: "Resume" });
  }
  return actions;
}

function LifecycleActions({
  range,
  onAction,
}: Readonly<{ range: RangePresentation; onAction: (dialog: LifecycleDialog) => void }>) {
  const actions = lifecycleActionsFor(range);
  if (actions.length === 0) return null;
  return (
    <div className="flex shrink-0 items-center gap-2">
      {actions.map((action) => (
        <Button
          key={action.dialog}
          variant={action.dialog === "pause" || action.dialog === "resume" ? "outline" : "destructive"}
          size="sm"
          onClick={() => onAction(action.dialog)}
        >
          {action.label}
        </Button>
      ))}
    </div>
  );
}

/**
 * Renders one active/live range: status chip + realtime socket, the shared
 * instance table, and lifecycle actions behind confirmation. Extracted from
 * `RangeDashboardPage` (#1370) so the dashboard and the range-detail page's
 * active-range branch present the exact same live view instead of two
 * surfaces drifting apart. `title`/`description` are supplied by the caller
 * since the two surfaces use different page copy.
 */
export function ActiveRangePanel({
  range,
  isFetching,
  title,
  description,
}: Readonly<{ range: RangePresentation; isFetching: boolean; title: string; description: string }>) {
  const [dialog, setDialog] = useState<LifecycleDialog>(null);
  const rangeStatusSocket = useRangeStatusSocket(range.request_id);

  const cancelRange = useCancelRange();
  const destroyRange = useDestroyRange();
  const pauseRange = usePauseRange();
  const resumeRange = useResumeRange();

  const mutations = { cancel: cancelRange, destroy: destroyRange, pause: pauseRange, resume: resumeRange } as const;
  const activeMutation = dialog ? mutations[dialog] : null;

  function closeDialog() {
    cancelRange.reset();
    destroyRange.reset();
    pauseRange.reset();
    resumeRange.reset();
    setDialog(null);
  }

  const mapping = rangeStatusMapping(range.status);
  const transient = TRANSIENT_STATUSES.has(range.status);

  return (
    <>
      <PageHeader
        title={title}
        description={description}
        actions={
          <>
            <LiveIndicator connected={rangeStatusSocket.connected} />
            <StatusChip intent={mapping.intent} label={mapping.label} />
          </>
        }
      />

      {transient ? (
        <div className="mb-4 max-w-xs" aria-hidden="true">
          <Progress value={66} className="h-1.5 animate-pulse" />
        </div>
      ) : null}

      <Card className="mb-6 overflow-hidden py-0" aria-busy={isFetching}>
        <InstanceTable instances={range.instances} />
      </Card>

      <div className="flex items-center justify-between gap-4">
        <dl className="text-sm text-muted-foreground">
          <div className="flex gap-2">
            <dt className="font-medium text-foreground">Scenario</dt>
            <dd>{range.scenario_id}</dd>
          </div>
        </dl>
        <LifecycleActions range={range} onAction={setDialog} />
      </div>

      {(Object.keys(DIALOG_COPY) as Array<Exclude<LifecycleDialog, null>>).map((key) => (
        <ConfirmDialog
          key={key}
          open={dialog === key}
          title={DIALOG_COPY[key].title}
          confirmLabel={DIALOG_COPY[key].confirmLabel}
          destructive={key === "cancel" || key === "destroy"}
          pending={dialog === key && activeMutation?.isPending}
          error={dialog === key ? activeMutation?.error : undefined}
          onOpenChange={(open) => {
            if (!open) closeDialog();
          }}
          onConfirm={() => {
            mutations[key].mutate({ request_id: range.request_id }, { onSuccess: closeDialog });
          }}
        >
          {DIALOG_COPY[key].description}
        </ConfirmDialog>
      ))}
    </>
  );
}
