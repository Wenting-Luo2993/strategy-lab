"use client";

import { useId, useState, type ReactNode } from "react";
import {
  Button,
  DrawerBody,
  DrawerHeader,
  DrawerHeaderTitle,
  OverlayDrawer,
  Tooltip,
} from "@fluentui/react-components";
import {
  BeakerRegular,
  ChevronRightRegular,
  DataTrendingRegular,
  DismissRegular,
  NavigationRegular,
} from "@fluentui/react-icons";
import { dashboardHref } from "@/data/researchPresentation";

type DashboardSection = "live" | "research";
type DashboardTheme = "light" | "dark";

export function DashboardSectionMenu({
  current,
  theme,
}: {
  current: DashboardSection;
  theme: DashboardTheme;
}) {
  const [open, setOpen] = useState(false);
  const drawerId = useId();

  return (
    <>
      <div
        className="fixed z-40"
        style={{
          right: "max(1rem, env(safe-area-inset-right))",
          top: "max(1rem, env(safe-area-inset-top))",
        }}
      >
        <Tooltip content="Open dashboard navigation" relationship="label">
          <Button
            appearance="primary"
            aria-controls={drawerId}
            aria-expanded={open}
            aria-haspopup="dialog"
            aria-label="Open dashboard navigation"
            icon={<NavigationRegular />}
            onClick={() => setOpen(true)}
            shape="circular"
            size="large"
            style={{
              backgroundColor: "var(--accent)",
              boxShadow: "0 10px 28px rgba(14, 39, 31, 0.24)",
            }}
          />
        </Tooltip>
      </div>

      <OverlayDrawer
        aria-label="Dashboard sections"
        backdrop={{
          style: {
            backdropFilter: "blur(2px)",
            backgroundColor: "rgba(10, 20, 16, 0.48)",
          },
        }}
        className="dashboard-navigation"
        data-theme={theme}
        id={drawerId}
        modalType="modal"
        onOpenChange={(_, data) => setOpen(data.open)}
        open={open}
        position="end"
        size="small"
        style={{
          backgroundColor: "var(--surface)",
          color: "var(--foreground)",
          height: "100dvh",
          maxWidth: "92vw",
          width: "26rem",
        }}
      >
        <DrawerHeader className="border-b border-[var(--border)] px-7 pb-6 pt-7">
          <DrawerHeaderTitle
            action={
              <Button
                appearance="subtle"
                aria-label="Close dashboard navigation"
                icon={<DismissRegular />}
                onClick={() => setOpen(false)}
                shape="circular"
                size="large"
                style={{ color: "var(--foreground)" }}
              />
            }
          >
            <div>
              <p className="text-xs font-bold uppercase tracking-[0.18em] text-[var(--accent)]">
                Strategy Lab
              </p>
              <h2 className="mt-2 text-2xl font-bold tracking-tight">
                Explore dashboards
              </h2>
              <p className="mt-2 max-w-xs text-sm leading-6 text-[var(--muted)]">
                Move between live operations and published backtest research.
              </p>
            </div>
          </DrawerHeaderTitle>
        </DrawerHeader>

        <DrawerBody className="flex flex-col px-5 py-6">
          <nav aria-label="Dashboard navigation" className="grid gap-3">
            <SectionLink
              active={current === "live"}
              description="Monitor accounts, positions, execution, and performance."
              href={dashboardHref("/")}
              icon={<DataTrendingRegular />}
              label="Live Trading"
              onNavigate={() => setOpen(false)}
            />
            <SectionLink
              active={current === "research"}
              description="Review validated summaries, evidence, and lineage."
              href={dashboardHref("/research/")}
              icon={<BeakerRegular />}
              label="Backtest Research"
              onNavigate={() => setOpen(false)}
            />
          </nav>

          <div className="mt-auto border-t border-[var(--border)] px-2 pt-5">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
              Research surfaces are read only
            </p>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
              Detailed local artifacts remain outside the published dashboard.
            </p>
          </div>
        </DrawerBody>
      </OverlayDrawer>
    </>
  );
}

function SectionLink({
  active,
  description,
  href,
  icon,
  label,
  onNavigate,
}: {
  active: boolean;
  description: string;
  href: string;
  icon: ReactNode;
  label: string;
  onNavigate: () => void;
}) {
  return (
    <a
      aria-current={active ? "page" : undefined}
      className={`group grid grid-cols-[auto_1fr_auto] items-center gap-4 rounded-xl border px-4 py-5 transition ${
        active
          ? "border-[var(--accent)] bg-[color-mix(in_srgb,var(--accent)_12%,var(--surface))]"
          : "border-[var(--border)] bg-[var(--surface-muted)] hover:border-[var(--accent)]"
      }`}
      href={href}
      onClick={onNavigate}
    >
      <span
        aria-hidden="true"
        className={`grid size-11 place-items-center rounded-full ${
          active
            ? "bg-[var(--accent)] text-white"
            : "bg-[var(--surface)] text-[var(--accent)]"
        }`}
      >
        {icon}
      </span>
      <span>
        <span className="block text-base font-bold">{label}</span>
        <span className="mt-1 block text-sm leading-5 text-[var(--muted)]">
          {description}
        </span>
      </span>
      <ChevronRightRegular
        aria-hidden="true"
        className="text-[var(--muted)] transition-transform group-hover:translate-x-1"
      />
    </a>
  );
}
