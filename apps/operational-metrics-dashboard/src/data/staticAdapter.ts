import { fixtureByName } from "./fixtures";
import type { DashboardData } from "./types";

export function getStaticDashboardData(): DashboardData {
  return fixtureByName(process.env.NEXT_PUBLIC_DASHBOARD_FIXTURE);
}