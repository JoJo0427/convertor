import type { PlanDetails } from "./types";

/** Shared by the action button and keyboard shortcut. A plan must describe the
 * exact current inputs, target and options before either can submit a job. */
export function canStartConversion({
  target, fileCount, busy, hasJob, details, planIsCurrent,
}: {
  target: string | null;
  fileCount: number;
  busy: boolean;
  hasJob: boolean;
  details: PlanDetails | null;
  planIsCurrent: boolean;
}): boolean {
  return Boolean(
    target && fileCount > 0 && !busy && !hasJob && planIsCurrent && details &&
    details.route.available &&
    !details.warnings.some((notice) => notice.level === "error") &&
    details.compatibility.some((entry) => entry.compatible),
  );
}
