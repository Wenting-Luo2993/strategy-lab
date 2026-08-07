import fs from "node:fs";
import path from "node:path";
import type { StrategyConfigSummary } from "./types";

const defaultRuleset = "orb_exp073_paper_burn_in";

export function getActiveRulesetSummary(): StrategyConfigSummary | null {
  const rulesetName = process.env.NEXT_PUBLIC_ACTIVE_RULESET ?? process.env.ACTIVE_RULESET ?? defaultRuleset;
  const rulesetPath = resolveRulesetPath(rulesetName);
  if (!rulesetPath) {
    return null;
  }

  const source = fs.readFileSync(rulesetPath, "utf8");
  return {
    name: scalar(source, "name") ?? rulesetName,
    version: scalar(source, "version") ?? "--",
    description: scalar(source, "description") ?? "--",
    symbols: inlineList(source, "symbols"),
    timeframe: nestedScalar(source, "instruments", "timeframe") ?? "--",
    strategyType: nestedScalar(source, "strategy", "type") ?? "--",
    breakoutEvaluation: nestedScalar(source, "strategy", "breakout_evaluation"),
    positionSizeMethod: nestedScalar(source, "position_size", "method") ?? "--",
    maxShares: numberValue(nestedScalar(source, "position_size", "max_shares")),
    maxPositionPct: numberValue(nestedScalar(source, "position_size", "max_position_pct")),
  };
}

function resolveRulesetPath(rulesetName: string): string | null {
  const fileName = rulesetName.endsWith(".yaml") ? rulesetName : `${rulesetName}.yaml`;
  const candidates = [
    path.resolve(process.cwd(), "../../vibe/rulesets", fileName),
    path.resolve(process.cwd(), "vibe/rulesets", fileName),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? null;
}

function scalar(source: string, key: string): string | undefined {
  const match = source.match(new RegExp(`^${escapeRegExp(key)}:\\s*(.+)$`, "m"));
  return match ? clean(match[1]) : undefined;
}

function nestedScalar(source: string, section: string, key: string): string | undefined {
  const lines = source.split(/\r?\n/);
  const sectionIndex = lines.findIndex((line) => line.trim() === `${section}:`);
  if (sectionIndex === -1) {
    return undefined;
  }
  for (const line of lines.slice(sectionIndex + 1)) {
    if (line.length > 0 && !line.startsWith(" ")) {
      break;
    }
    const match = line.match(new RegExp(`^\\s+${escapeRegExp(key)}:\\s*(.+)$`));
    if (match) {
      return clean(match[1]);
    }
  }
  return undefined;
}

function inlineList(source: string, key: string): string[] {
  const value = nestedScalar(source, "instruments", key) ?? scalar(source, key);
  if (!value) {
    return [];
  }
  if (value.startsWith("[") && value.endsWith("]")) {
    return value.slice(1, -1).split(",").map((item) => clean(item)).filter(Boolean);
  }
  return [clean(value)].filter(Boolean);
}

function clean(value: string): string {
  return value.trim().replace(/^['\"]|['\"]$/g, "");
}

function numberValue(value: string | undefined): number | null {
  if (!value) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
