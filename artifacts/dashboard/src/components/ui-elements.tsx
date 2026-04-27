import { cn } from "@/lib/utils";
import { TrendingUp, TrendingDown, Info } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/* ── StatCard ────────────────────────────────────────────────────── */
export function StatCard({
  title,
  value,
  subtitle,
  trend,
  tooltip,
  className,
}: {
  title: string;
  value: React.ReactNode;
  subtitle?: string;
  trend?: "up" | "down" | "neutral";
  tooltip?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "p-5 rounded-sm relative overflow-hidden group",
        className
      )}
      style={{
        border: "1px solid var(--color-app-border)",
        background: "var(--color-app-surface)",
      }}
    >
      {/* Header row */}
      <div className="flex items-center justify-between mb-2">
        <h3 className="mono-micro" style={{ color: "var(--color-text-secondary)" }}>
          {title}
        </h3>
        <div className="flex items-center gap-1.5">
          {trend === "up" && (
            <TrendingUp size={12} style={{ color: "var(--color-accent-success)" }} />
          )}
          {trend === "down" && (
            <TrendingDown size={12} style={{ color: "var(--color-accent-danger)" }} />
          )}
          {tooltip && (
            <TooltipProvider delayDuration={200}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className="transition-colors focus:outline-none"
                    style={{ color: "var(--color-text-tertiary)" }}
                    tabIndex={-1}
                  >
                    <Info size={12} />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" className="max-w-[220px] text-center leading-relaxed">
                  {tooltip}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
        </div>
      </div>

      {/* Value */}
      <div
        className="text-3xl font-light tracking-tight font-mono"
        style={{ color: "var(--color-text-primary)" }}
      >
        {value}
      </div>

      {/* Subtitle */}
      {subtitle && (
        <p
          className="mt-2 text-xs transition-colors"
          style={{ color: "var(--color-text-tertiary)" }}
        >
          {subtitle}
        </p>
      )}

      {/* Decorative corner accent on hover */}
      <div className="absolute top-0 right-0 w-8 h-8 opacity-0 group-hover:opacity-100 transition-opacity">
        <div
          className="absolute top-0 right-0 w-3 h-3 m-2"
          style={{
            borderTop: "1px solid var(--color-text-tertiary)",
            borderRight: "1px solid var(--color-text-tertiary)",
          }}
        />
      </div>
    </div>
  );
}

/* ── Badge ───────────────────────────────────────────────────────── */
export function Badge({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "px-2 py-0.5 rounded-sm text-[10px] uppercase font-mono font-medium border",
        className
      )}
    >
      {children}
    </span>
  );
}

/* ── TableSkeleton ───────────────────────────────────────────────── */
export function TableSkeleton() {
  return (
    <div className="space-y-3 p-2">
      {[1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="animate-pulse flex items-center gap-4">
          <div
            className="h-3 w-1/4 rounded-sm"
            style={{ background: "var(--color-app-border)" }}
          />
          <div
            className="h-3 w-1/2 rounded-sm"
            style={{ background: "var(--color-app-border)" }}
          />
          <div
            className="h-3 w-16 rounded-sm ml-auto"
            style={{ background: "var(--color-app-border)" }}
          />
        </div>
      ))}
    </div>
  );
}

/* ── TerminalPanel ───────────────────────────────────────────────── */
export function TerminalPanel({
  title,
  children,
  rightAction,
  className,
}: {
  title: string;
  children: React.ReactNode;
  rightAction?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn("flex flex-col rounded-sm overflow-hidden", className)}
      style={{
        border: "1px solid var(--color-app-border)",
        background: "var(--color-app-surface)",
      }}
    >
      <div
        className="flex items-center justify-between px-4 py-2 shrink-0"
        style={{
          borderBottom: "1px solid var(--color-app-border)",
          background: "var(--color-panel-header)",
        }}
      >
        <span className="mono-micro" style={{ color: "var(--color-text-secondary)" }}>
          {title}
        </span>
        {rightAction && (
          <div className="mono-micro" style={{ color: "var(--color-text-secondary)" }}>
            {rightAction}
          </div>
        )}
      </div>
      <div className="flex-1 relative">{children}</div>
    </div>
  );
}

/* ── Sparkline ───────────────────────────────────────────────────── */
export function Sparkline({
  data,
  color,
  width = 100,
  height = 30,
}: {
  data: number[];
  color: string;
  width?: number;
  height?: number;
}) {
  if (!data || data.length === 0) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const points = data
    .map((d, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - ((d - min) / range) * height;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
    >
      <polyline
        fill="none"
        stroke={color}
        strokeWidth="1.5"
        vectorEffect="non-scaling-stroke"
        points={points}
      />
    </svg>
  );
}
