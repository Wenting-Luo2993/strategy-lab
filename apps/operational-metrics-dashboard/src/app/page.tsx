import { DashboardApp } from "@/components/DashboardApp";
import { getStaticDashboardData } from "@/data/staticAdapter";

export default function Home() {
  const initialData = getStaticDashboardData();

  return <DashboardApp initialData={initialData} />;
}
