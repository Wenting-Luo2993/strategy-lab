import { getStaticDashboardData } from "./staticAdapter";
import { getSupabaseDashboardData } from "./supabaseAdapter";
import type { DashboardData } from "./types";

export async function loadDashboardData(): Promise<DashboardData> {
  if (process.env.NEXT_PUBLIC_DASHBOARD_DATA_SOURCE === "supabase") {
    return getSupabaseDashboardData();
  }
  return getStaticDashboardData();
}