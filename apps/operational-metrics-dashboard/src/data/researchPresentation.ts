import type { ResearchPublication } from "./researchTypes.ts";

export type TrustBadge = {
  label: string;
  tone: "good" | "warning" | "danger" | "neutral";
};

export function trustBadges(publication: ResearchPublication): TrustBadge[] {
  const badges: TrustBadge[] = [];
  if (publication.compatibility !== "supported") {
    badges.push({ label: publication.compatibility === "unsupported" ? "Unsupported schema" : "Invalid payload", tone: "danger" });
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
