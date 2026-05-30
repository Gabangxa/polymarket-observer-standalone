import { useState, useEffect } from "react";
import { Link, useLocation } from "wouter";
import { motion } from "framer-motion";
import {
  Activity,
  BarChart2,
  Database,
  LayoutDashboard,
  TrendingUp,
  BookOpen,
  Radio,
  Moon,
  Sun,
  Settings,
  Globe,
  Zap,
  Bell,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useLiveHealth } from "@/hooks/use-polymarket";
import { useTimezone, TIMEZONES } from "@/hooks/use-timezone";
import { useAlertSettings } from "@/hooks/use-alert-settings";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";

const NAV_ITEMS = [
  { href: "/",             label: "Overview",    icon: LayoutDashboard },
  { href: "/markets",      label: "Markets",     icon: BarChart2 },
  { href: "/signals",      label: "Signals",     icon: Activity, badge: "live" },
  { href: "/performance",  label: "Performance", icon: TrendingUp },
  { href: "/execution",    label: "Execution",   icon: Zap },
  { href: "/snapshots",    label: "Data Feed",   icon: Database },
  { href: "/docs",         label: "Guide & FAQ", icon: BookOpen },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  const { data: health, isError } = useLiveHealth();
  const [isDark, setIsDark] = useState(true);
  const { timezone, setTimezone } = useTimezone();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { settings, set } = useAlertSettings();

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.remove("light");
    } else {
      document.documentElement.classList.add("light");
    }
  }, [isDark]);

  return (
    <div
      className="flex h-screen overflow-hidden font-sans"
      style={{ background: "var(--color-app-bg)", color: "var(--color-text-primary)" }}
    >
      {/* ── Sidebar ───────────────────────────────────────────────── */}
      <aside
        className="w-16 lg:w-64 flex flex-col shrink-0 transition-all duration-300 z-20"
        style={{
          borderRight: "1px solid var(--color-app-border)",
          background: "var(--color-app-surface)",
        }}
      >
        {/* Logo */}
        <div
          className="h-16 flex items-center justify-center lg:justify-start lg:px-6 shrink-0"
          style={{ borderBottom: "1px solid var(--color-app-border)" }}
        >
          <div
            className="w-8 h-8 rounded-sm flex items-center justify-center shrink-0"
            style={{
              border: "1px solid var(--color-accent-primary)",
              background: "color-mix(in srgb, var(--color-accent-primary) 10%, transparent)",
            }}
          >
            <Radio className="w-5 h-5" style={{ color: "var(--color-accent-primary)" }} />
          </div>
          <span
            className="ml-3 text-sm font-semibold tracking-tight hidden lg:block"
            style={{ color: "var(--color-text-primary)" }}
          >
            Observer
          </span>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-4 flex flex-col gap-1 px-2">
          {NAV_ITEMS.map((item) => {
            const isActive =
              location === item.href ||
              (item.href !== "/" && location.startsWith(item.href));

            return (
              <Link key={item.href} href={item.href}>
                <div
                  className={cn(
                    "flex items-center gap-3 px-3 py-2.5 rounded-sm transition-colors duration-150 relative cursor-pointer select-none",
                    isActive
                      ? "text-[var(--color-text-primary)] bg-[var(--color-app-surface-hover)]"
                      : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:bg-[var(--color-app-surface-hover)]/50"
                  )}
                >
                  {isActive && (
                    <motion.div
                      layoutId="sidebar-active-indicator"
                      className="absolute left-0 top-1/2 -translate-y-1/2 rounded-r"
                      style={{
                        width: 2,
                        height: 24,
                        background: "var(--color-accent-primary)",
                      }}
                    />
                  )}
                  <item.icon
                    className="w-5 h-5 shrink-0"
                    style={{
                      color: isActive ? "var(--color-accent-primary)" : "inherit",
                    }}
                  />
                  <span className="hidden lg:block text-sm font-medium">
                    {item.label}
                  </span>
                  {item.badge && (
                    <span
                      className="hidden lg:flex ml-auto text-[10px] font-mono px-1.5 py-0.5 rounded-sm"
                      style={{
                        background:
                          "color-mix(in srgb, var(--color-accent-primary) 15%, transparent)",
                        color: "var(--color-accent-primary)",
                        border:
                          "1px solid color-mix(in srgb, var(--color-accent-primary) 30%, transparent)",
                      }}
                    >
                      {item.badge}
                    </span>
                  )}
                </div>
              </Link>
            );
          })}
        </nav>

        {/* Bottom panel */}
        <div
          className="flex flex-col gap-1 p-2"
          style={{ borderTop: "1px solid var(--color-app-border)" }}
        >
          {/* Live status — desktop only */}
          <div className="hidden lg:block px-3 py-2 mb-1">
            <div className="flex items-center gap-2 mb-2">
              <span
                className={cn(
                  "inline-flex w-1.5 h-1.5 rounded-full",
                  isError
                    ? "bg-[var(--color-accent-danger)]"
                    : "bg-[var(--color-accent-success)] animate-pulse"
                )}
              />
              <span className="mono-micro" style={{ color: "var(--color-text-tertiary)" }}>
                System Status
              </span>
            </div>
            {health ? (
              <div
                className="space-y-1 font-mono text-[11px]"
                style={{ color: "var(--color-text-secondary)" }}
              >
                <div className="flex justify-between">
                  <span style={{ color: "var(--color-text-tertiary)" }}>Markets</span>
                  <span>{(health as any).markets}</span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: "var(--color-text-tertiary)" }}>Signals</span>
                  <span>{(health as any).signals}</span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: "var(--color-text-tertiary)" }}>Snapshots</span>
                  <span>{(health as any).snapshots?.toLocaleString()}</span>
                </div>
              </div>
            ) : (
              <div
                className="text-[11px] font-mono animate-pulse"
                style={{ color: "var(--color-text-tertiary)" }}
              >
                Connecting...
              </div>
            )}
          </div>

          {/* Timezone picker — desktop only */}
          <div className="hidden lg:flex items-center gap-2 px-3 py-2">
            <Globe
              className="w-5 h-5 shrink-0"
              style={{ color: "var(--color-text-tertiary)" }}
            />
            <select
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className="flex-1 text-sm font-medium bg-transparent border-0 outline-none cursor-pointer appearance-none"
              style={{ color: "var(--color-text-tertiary)" }}
            >
              {TIMEZONES.map((tz) => (
                <option
                  key={tz.value}
                  value={tz.value}
                  style={{
                    background: "var(--color-app-surface)",
                    color: "var(--color-text-primary)",
                  }}
                >
                  {tz.label}
                </option>
              ))}
            </select>
          </div>

          {/* Theme toggle */}
          <button
            onClick={() => setIsDark(!isDark)}
            className="flex items-center justify-center lg:justify-start px-3 py-2.5 rounded-sm transition-colors cursor-pointer hover:bg-[var(--color-app-surface-hover)]"
            style={{ color: "var(--color-text-tertiary)" }}
          >
            {isDark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
            <span className="hidden lg:block ml-3 text-sm font-medium">
              {isDark ? "Light Mode" : "Dark Mode"}
            </span>
          </button>

          <button
            onClick={() => setSettingsOpen(true)}
            className="flex items-center justify-center lg:justify-start px-3 py-2.5 rounded-sm transition-colors cursor-pointer hover:bg-[var(--color-app-surface-hover)]"
            style={{ color: "var(--color-text-tertiary)" }}
          >
            <Settings className="w-5 h-5" />
            <span className="hidden lg:block ml-3 text-sm font-medium">Settings</span>
          </button>

          <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
            <SheetContent
              side="left"
              className="w-80"
              style={{
                background: "var(--color-app-surface)",
                borderRight: "1px solid var(--color-app-border)",
                color: "var(--color-text-primary)",
              }}
            >
              <SheetHeader className="mb-6">
                <SheetTitle style={{ color: "var(--color-text-primary)" }}>
                  Settings
                </SheetTitle>
              </SheetHeader>

              {/* ── Alerts section ───────────────────────────── */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 mb-3">
                  <Bell className="w-4 h-4" style={{ color: "var(--color-accent-primary)" }} />
                  <span className="text-sm font-semibold tracking-wide uppercase"
                    style={{ color: "var(--color-text-secondary)", letterSpacing: "0.08em" }}>
                    Alerts
                  </span>
                </div>

                <p className="text-xs mb-4" style={{ color: "var(--color-text-tertiary)" }}>
                  Choose which execution events trigger a toast notification.
                </p>

                <div className="space-y-5">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <Label htmlFor="alert-failed" className="text-sm font-medium cursor-pointer"
                        style={{ color: "var(--color-text-primary)" }}>
                        Execution failed
                      </Label>
                      <p className="text-xs mt-0.5" style={{ color: "var(--color-text-tertiary)" }}>
                        Order rejected or errored on the exchange
                      </p>
                    </div>
                    <Switch
                      id="alert-failed"
                      checked={settings.onExecFailed}
                      onCheckedChange={(v) => set("onExecFailed", v)}
                    />
                  </div>

                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <Label htmlFor="alert-opened" className="text-sm font-medium cursor-pointer"
                        style={{ color: "var(--color-text-primary)" }}>
                        Order opened
                      </Label>
                      <p className="text-xs mt-0.5" style={{ color: "var(--color-text-tertiary)" }}>
                        Order successfully submitted to the CLOB
                      </p>
                    </div>
                    <Switch
                      id="alert-opened"
                      checked={settings.onExecOpened}
                      onCheckedChange={(v) => set("onExecOpened", v)}
                    />
                  </div>

                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <Label htmlFor="alert-closed" className="text-sm font-medium cursor-pointer"
                        style={{ color: "var(--color-text-primary)" }}>
                        Order filled
                      </Label>
                      <p className="text-xs mt-0.5" style={{ color: "var(--color-text-tertiary)" }}>
                        Order fully filled on the exchange
                      </p>
                    </div>
                    <Switch
                      id="alert-closed"
                      checked={settings.onExecClosed}
                      onCheckedChange={(v) => set("onExecClosed", v)}
                    />
                  </div>
                </div>

                <div
                  className="mt-6 pt-4 text-xs font-mono"
                  style={{
                    borderTop: "1px solid var(--color-app-border)",
                    color: "var(--color-text-tertiary)",
                  }}
                >
                  Preferences saved to local storage.
                </div>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </aside>

      {/* ── Main content ──────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col h-full min-w-0 relative">
        {/* Subtle top gradient */}
        <div
          className="absolute top-0 left-0 right-0 h-24 pointer-events-none z-0"
          style={{
            background:
              "linear-gradient(to bottom, color-mix(in srgb, var(--color-app-surface) 20%, transparent), transparent)",
          }}
        />

        <main className="flex-1 overflow-auto p-6 lg:p-10 relative z-10">
          <div className="max-w-7xl mx-auto space-y-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
