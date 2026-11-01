import type { ResearchPublication, ResearchPublicationKind } from "./researchTypes.ts";

export type TrustBadge = {
  label: string;
  tone: "good" | "warning" | "danger" | "neutral";
};

export type ResearchStatusOption = readonly [value: string, label: string];

const commonStatusOption: ResearchStatusOption = ["", "Any status"];
const statusOptionsByKind: Record<ResearchPublicationKind, readonly ResearchStatusOption[]> = {
  run_summary: [["completed", "Completed"], ["failed", "Failed"]],
  registry_record: [["validated", "Validated"], ["validation_failed", "Validation failed"]],
  experiment_definition: [["running", "Running"]],
  experiment_membership: [["train", "Train"], ["validation", "Validation"], ["test", "Test"]],
  experiment_outcome: [
    ["completed", "Completed"],
    ["inconclusive", "Inconclusive"],
    ["execution_failed", "Execution failed"],
  ],
  study_reliability: [
    ["reliable", "Reliable"],
    ["rejected", "Rejected"],
    ["inconclusive", "Inconclusive"],
    ["review_required", "Review required"],
    ["not_assessed", "Not assessed"],
  ],
  methodology_freeze: [["frozen", "Frozen"]],
  holdout_authorization: [["authorized", "Authorized"]],
  holdout_claim: [["claimed", "Claimed"]],
  holdout_terminal: [["completed", "Completed"], ["interrupted", "Interrupted"]],
  experiment_archive: [["archived", "Archived"]],
};

export function researchStatusOptions(
  kind: ResearchPublicationKind | "all",
): readonly ResearchStatusOption[] {
  if (kind !== "all") return [commonStatusOption, ...statusOptionsByKind[kind]];
  const unique = new Map<string, string>();
  Object.values(statusOptionsByKind).flat().forEach(([value, label]) => {
    unique.set(value, label);
  });
  return [
    commonStatusOption,
    ...[...unique.entries()]
      .sort((left, right) => left[1].localeCompare(right[1])),
  ];
}

export function trustBadges(publication: ResearchPublication): TrustBadge[] {
  const badges: TrustBadge[] = [];
  if (publication.compatibility !== "supported") {
    badges.push({ label: publication.compatibility === "unsupported" ? "Unsupported schema" : "Invalid payload", tone: "danger" });
  }
  if (publication.kind !== "run_summary" && publication.kind !== "registry_record") {
    badges.push({
      label: publication.kind.replaceAll("_", " "),
      tone: "neutral",
    });
    badges.push({
      label: publication.status === "reliable"
        ? "Eligible for user-authorized holdout"
        : (publication.status ?? "Not assessed").replaceAll("_", " "),
      tone: publication.status === "reliable" || publication.status === "completed"
        ? "good"
        : publication.status === "rejected" || publication.status === "interrupted"
          ? "danger"
          : "warning",
    });
    return badges;
  }
  badges.push({
    label: publication.methodologyVersion ?? "Methodology unknown",
    tone: publication.methodologyVersion === "legacy-uncontrolled" ? "danger" : "good",
  });
  if (publication.executionModelVersion === null) {
    badges.push({ label: "Execution model unknown", tone: "danger" });
  } else if (publication.executionModelVersion < 6) {
    badges.push({ label: `Pre-P2b execution v${publication.executionModelVersion}`, tone: "warning" });
  } else {
    badges.push({ label: `Execution model v${publication.executionModelVersion}`, tone: "good" });
  }
  publication.badges
    .filter((badge) => !/^execution-model-v\d+$/i.test(badge))
    .filter((badge) => badge.toLowerCase() !== "pre-p2b")
    .filter((badge) => !badges.some((item) => item.label === badge))
    .forEach((badge) => {
      const normalized = badge.toLowerCase();
      badges.push({
        label: badge.replaceAll("-", " "),
        tone: normalized.includes("legacy") || normalized.includes("unknown")
          ? "danger"
          : normalized.includes("survivorship") || normalized.includes("pre-p2b")
            ? "warning"
            : "neutral",
      });
    });
  if (publication.kind === "run_summary") {
    const findings = publication.validation.findings;
    badges.push({
      label: findings.blocking > 0
        ? `${findings.blocking} blocking finding${findings.blocking === 1 ? "" : "s"}`
        : publication.validation.leakagePassed === false ? "Leakage check failed" : "No blocking findings",
      tone: findings.blocking > 0 || publication.validation.leakagePassed === false ? "danger" : "good",
    });
    if (publication.codeDirty) {
      badges.push({ label: "Dirty code", tone: "warning" });
    }
  }
  return badges;
}

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}

export function dashboardHref(path: "/" | "/research/"): string {
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH?.replace(/\/$/, "") ?? "";
  return `${basePath}${path}`;
}
