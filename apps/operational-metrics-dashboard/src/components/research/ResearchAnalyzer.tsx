"use client";

import { useEffect, useRef } from "react";
import type { ResearchDataAdapter } from "@/data/researchTypes";

type WorkspaceElement = HTMLElement & {
  adapter: ResearchDataAdapter;
};

export function ResearchAnalyzer({ adapter }: { adapter: ResearchDataAdapter }) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let workspace: WorkspaceElement | null = null;

    import("./research-analysis-workspace.mjs")
      .then(() => {
        if (cancelled || !host.current) return;
        workspace = document.createElement("research-analysis-workspace") as WorkspaceElement;
        workspace.setAttribute("storage-key", "research-analysis-v2");
        workspace.adapter = adapter;
        host.current.replaceChildren(workspace);
      });

    return () => {
      cancelled = true;
      workspace?.remove();
    };
  }, [adapter]);

  return <div ref={host} />;
}
