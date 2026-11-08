import { ResearchDashboardApp } from "@/components/research/ResearchDashboardApp";
import { FixtureResearchDataAdapter } from "@/data/researchFixtureAdapter";

export const metadata = {
  title: "Backtest Research Dashboard",
  description: "Read-only published backtest methodology, validation, evidence, and lineage.",
};

export default async function ResearchPage() {
  const initialData = process.env.NEXT_PUBLIC_RESEARCH_DATA_SOURCE === "supabase"
    ? null
    : await loadFixtureData();
  return <ResearchDashboardApp initialData={initialData} />;
}

async function loadFixtureData() {
  const adapter = new FixtureResearchDataAdapter();
  const [overview, page] = await Promise.all([
    adapter.getOverview(),
    adapter.listPublications({
      page: 1,
      pageSize: 10,
      kind: "all",
      sort: "acknowledged_at",
      direction: "desc",
    }),
  ]);
  return { overview, page };
}
