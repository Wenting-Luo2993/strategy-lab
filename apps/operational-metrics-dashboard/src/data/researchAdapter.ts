import { FixtureResearchDataAdapter } from "./researchFixtureAdapter.ts";
import { SupabaseResearchDataAdapter } from "./researchSupabaseAdapter.ts";
import type { ResearchDataAdapter } from "./researchTypes.ts";

export function createResearchDataAdapter(
  environment: Record<string, string | undefined> = {
    NEXT_PUBLIC_RESEARCH_DATA_SOURCE:
      process.env.NEXT_PUBLIC_RESEARCH_DATA_SOURCE,
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY:
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  },
): ResearchDataAdapter {
  if (environment.NEXT_PUBLIC_RESEARCH_DATA_SOURCE === "supabase") {
    const url = environment.NEXT_PUBLIC_SUPABASE_URL;
    const anonKey = environment.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !anonKey) {
      throw new Error("Research Supabase public URL and anonymous key are not configured.");
    }
    return new SupabaseResearchDataAdapter(url, anonKey);
  }
  return new FixtureResearchDataAdapter();
}
