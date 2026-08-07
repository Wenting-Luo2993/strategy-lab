import { DashboardApp } from "@/components/DashboardApp";
import { getActiveRulesetSummary } from "@/data/rulesetSummary";
import { getStaticDashboardData } from "@/data/staticAdapter";

export default function Home() {
  const initialData = getStaticDashboardData();
  const strategyConfig = getActiveRulesetSummary();

  return <DashboardApp initialData={initialData} strategyConfig={strategyConfig} />;
}
