"use client";

import { useEffect, useId, useState } from "react";
import { dashboardHref } from "@/data/researchPresentation";

type DashboardSection = "live" | "research";

export function DashboardSectionMenu({
  current,
}: {
  current: DashboardSection;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <>
      <button
        aria-controls={panelId}
        aria-expanded={open}
        aria-label="Open dashboard navigation"
        className="rounded-md border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm font-semibold"
        onClick={() => setOpen(true)}
        type="button"
      >
        Menu
      </button>
      {open && (
        <div className="fixed inset-0 z-50">
          <button
            aria-label="Close dashboard navigation"
            className="absolute inset-0 bg-black/40"
            onClick={() => setOpen(false)}
            type="button"
          />
          <aside
            aria-label="Dashboard sections"
            aria-modal="true"
            className="absolute inset-y-0 right-0 flex w-[min(22rem,88vw)] flex-col border-l border-[var(--border)] bg-[var(--surface)] p-5 shadow-2xl"
            id={panelId}
            role="dialog"
          >
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase text-[var(--muted)]">
                  Strategy Lab
                </p>
                <h2 className="mt-1 text-xl font-bold">Dashboard navigation</h2>
              </div>
              <button
                aria-label="Close dashboard navigation"
                className="rounded-md border border-[var(--border)] px-3 py-2 text-sm font-semibold"
                onClick={() => setOpen(false)}
                type="button"
              >
                Close
              </button>
            </div>
            <nav className="mt-8 grid gap-3">
              <SectionLink
                active={current === "live"}
                href={dashboardHref("/")}
                label="Live Trading"
              />
              <SectionLink
                active={current === "research"}
                href={dashboardHref("/research/")}
                label="Backtest Research"
              />
            </nav>
          </aside>
        </div>
      )}
    </>
  );
}

function SectionLink({
  active,
  href,
  label,
}: {
  active: boolean;
  href: string;
  label: string;
}) {
  return (
    <a
      aria-current={active ? "page" : undefined}
      className={`rounded-lg border px-4 py-4 text-base font-semibold ${
        active
          ? "border-[var(--accent)] bg-[var(--accent)] text-white"
          : "border-[var(--border)] bg-[var(--surface-muted)]"
      }`}
      href={href}
    >
      {label}
    </a>
  );
}
