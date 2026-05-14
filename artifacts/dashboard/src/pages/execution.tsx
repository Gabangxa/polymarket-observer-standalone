import { useState } from "react";
import { motion } from "framer-motion";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Clock,
  DollarSign,
  Loader2,
  TrendingUp,
  Wallet,
  XCircle,
  Zap,
} from "lucide-react";
import {
  useLiveOrders,
  useLivePositions,
  useLivePortfolio,
  useCancelOrder,
  useCancelAllOrders,
} from "@/hooks/use-polymarket";
import { TableSkeleton, Badge } from "@/components/ui-elements";
import {
  formatRelativeTime,
  getStrategyColor,
  parseNumeric,
  formatPrice,
  cn,
} from "@/lib/utils";

// ── Status helpers ─────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<string, string> = {
  PENDING_SUBMISSION:
    "text-amber-400 bg-amber-400/10 border-amber-400/20",
  SUBMITTED:
    "text-sky-400 bg-sky-400/10 border-sky-400/20",
  PARTIALLY_FILLED:
    "text-indigo-400 bg-indigo-400/10 border-indigo-400/20",
  CANCEL_REQUESTED:
    "text-orange-400 bg-orange-400/10 border-orange-400/20 animate-pulse",
  FILLED:
    "text-emerald-400 bg-emerald-400/10 border-emerald-400/20",
  CANCELED:
    "text-slate-400 bg-slate-400/10 border-slate-400/20",
  REJECTED:
    "text-red-400 bg-red-400/10 border-red-400/20",
  EXPIRED:
    "text-slate-400 bg-slate-400/10 border-slate-400/20",
  ERROR:
    "text-red-400 bg-red-400/10 border-red-400/20",
};

function statusStyle(s: string) {
  return STATUS_STYLES[s] ?? "text-slate-400 bg-slate-400/10 border-slate-400/20";
}

function statusIcon(s: string) {
  if (s === "FILLED") return <CheckCircle2 className="w-3 h-3" />;
  if (s === "CANCELED" || s === "EXPIRED") return <Ban className="w-3 h-3" />;
  if (s === "REJECTED" || s === "ERROR") return <XCircle className="w-3 h-3" />;
  if (s === "CANCEL_REQUESTED") return <Loader2 className="w-3 h-3 animate-spin" />;
  if (s === "SUBMITTED" || s === "PARTIALLY_FILLED")
    return <Clock className="w-3 h-3" />;
  return null;
}

function closedAt(order: { filledAt?: string | null; canceledAt?: string | null }) {
  return order.filledAt ?? order.canceledAt ?? null;
}

function fillPct(filled: string | null | undefined, size: string): string {
  const f = parseNumeric(filled);
  const s = parseNumeric(size);
  if (s === 0) return "—";
  const pct = (f / s) * 100;
  return `${pct.toFixed(0)}%`;
}

// ── Stat card ──────────────────────────────────────────────────────────────────

function StatCard({
  icon,
  label,
  value,
  sub,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  sub?: string;
}) {
  return (
    <div
      className="terminal-panel flex items-center gap-4 px-5 py-4"
      style={{ minWidth: 160 }}
    >
      <div
        className="w-9 h-9 rounded-sm flex items-center justify-center shrink-0"
        style={{
          background: "color-mix(in srgb, var(--color-accent-primary) 10%, transparent)",
          border: "1px solid color-mix(in srgb, var(--color-accent-primary) 20%, transparent)",
        }}
      >
        <span style={{ color: "var(--color-accent-primary)" }}>{icon}</span>
      </div>
      <div>
        <div className="text-xs font-mono" style={{ color: "var(--color-text-tertiary)" }}>
          {label}
        </div>
        <div className="text-lg font-bold font-mono" style={{ color: "var(--color-text-primary)" }}>
          {value}
        </div>
        {sub && (
          <div className="text-[10px] font-mono" style={{ color: "var(--color-text-tertiary)" }}>
            {sub}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Confirm dialog ─────────────────────────────────────────────────────────────

function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  danger,
  loading,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onCancel} />
      <div
        className="relative z-10 rounded-sm p-6 max-w-sm w-full mx-4 space-y-4"
        style={{
          background: "var(--color-app-surface)",
          border: "1px solid var(--color-app-border)",
        }}
      >
        <div className="flex items-center gap-2">
          <AlertTriangle
            className="w-5 h-5 shrink-0"
            style={{ color: danger ? "var(--color-accent-danger)" : "var(--color-accent-warning)" }}
          />
          <h3 className="font-semibold text-sm" style={{ color: "var(--color-text-primary)" }}>
            {title}
          </h3>
        </div>
        <p className="text-sm font-mono" style={{ color: "var(--color-text-secondary)" }}>
          {body}
        </p>
        <div className="flex gap-3 justify-end">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm rounded-sm transition-colors"
            style={{
              background: "var(--color-app-surface-hover)",
              color: "var(--color-text-secondary)",
              border: "1px solid var(--color-app-border)",
            }}
          >
            Abort
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className="px-4 py-2 text-sm rounded-sm font-medium transition-colors flex items-center gap-2"
            style={{
              background: danger
                ? "color-mix(in srgb, var(--color-accent-danger) 20%, transparent)"
                : "color-mix(in srgb, var(--color-accent-primary) 20%, transparent)",
              color: danger ? "var(--color-accent-danger)" : "var(--color-accent-primary)",
              border: `1px solid ${danger ? "var(--color-accent-danger)" : "var(--color-accent-primary)"}`,
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Active order blotter ───────────────────────────────────────────────────────

function ActiveBlotter() {
  const { data, isLoading } = useLiveOrders({ status: "active" });
  const qc = useQueryClient();
  const cancelMutation = useCancelOrder({
    mutation: {
      onSuccess: () => qc.invalidateQueries({ queryKey: ["listOrders"] }),
    },
  });
  const [confirmId, setConfirmId] = useState<number | null>(null);

  const orders = data?.orders ?? [];

  return (
    <>
      <ConfirmDialog
        open={confirmId !== null}
        title="Cancel this order?"
        body="The order will be marked CANCEL_REQUESTED. The executor will send the cancel to the exchange on its next cycle."
        confirmLabel="Yes, cancel it"
        danger
        loading={cancelMutation.isPending}
        onConfirm={() => {
          if (confirmId !== null) {
            cancelMutation.mutate({ id: confirmId });
            setConfirmId(null);
          }
        }}
        onCancel={() => setConfirmId(null)}
      />

      <div className="overflow-x-auto">
        <table className="w-full text-sm text-left whitespace-nowrap">
          <thead
            className="text-xs font-mono uppercase"
            style={{
              color: "var(--color-text-tertiary)",
              background: "color-mix(in srgb, var(--color-app-surface-hover) 50%, transparent)",
              borderBottom: "1px solid var(--color-app-border)",
            }}
          >
            <tr>
              <th className="px-4 py-3 font-medium">Market</th>
              <th className="px-4 py-3 font-medium">Strategy</th>
              <th className="px-4 py-3 font-medium">Side</th>
              <th className="px-4 py-3 font-medium text-right">Size (USDC)</th>
              <th className="px-4 py-3 font-medium text-right">Entry Price</th>
              <th className="px-4 py-3 font-medium text-right">Filled Qty</th>
              <th className="px-4 py-3 font-medium text-right">Fill %</th>
              <th className="px-4 py-3 font-medium text-center">Status</th>
              <th className="px-4 py-3 font-medium">Age</th>
              <th className="px-4 py-3 font-medium">Exchange ID</th>
              <th className="px-4 py-3 font-medium text-center">Action</th>
            </tr>
          </thead>
          <tbody style={{ borderColor: "var(--color-app-border)" }} className="divide-y divide-[var(--color-app-border)]/50">
            {isLoading ? (
              <tr>
                <td colSpan={11} className="p-4">
                  <TableSkeleton />
                </td>
              </tr>
            ) : orders.length === 0 ? (
              <tr>
                <td
                  colSpan={11}
                  className="px-4 py-14 text-center font-mono text-sm"
                  style={{ color: "var(--color-text-tertiary)" }}
                >
                  No active orders
                </td>
              </tr>
            ) : (
              orders.map((o) => (
                <tr
                  key={o.id}
                  className="transition-colors hover:bg-[var(--color-app-surface-hover)]/40"
                >
                  <td
                    className="px-4 py-3 font-medium max-w-[280px] truncate"
                    style={{ color: "var(--color-text-primary)" }}
                    title={o.question ?? o.marketId}
                  >
                    {o.question ?? o.marketId}
                  </td>
                  <td className="px-4 py-3">
                    <Badge className={getStrategyColor(o.strategy)}>
                      {o.strategy.replace("_engine", "").replace(/_/g, "-")}
                    </Badge>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={cn(
                        "text-xs font-mono font-bold px-2 py-0.5 rounded-sm border",
                        o.side === "YES"
                          ? "text-emerald-400 bg-emerald-400/10 border-emerald-400/20"
                          : "text-red-400 bg-red-400/10 border-red-400/20",
                      )}
                    >
                      {o.side}
                    </span>
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono"
                    style={{ color: "var(--color-text-primary)" }}
                  >
                    ${parseNumeric(o.sizeUsdc).toFixed(2)}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono"
                    style={{ color: "var(--color-accent-primary)" }}
                  >
                    {formatPrice(o.price)}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    {parseNumeric(o.filledQty).toFixed(2)}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono text-xs"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    {fillPct(o.filledQty, o.sizeUsdc)}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <Badge className={statusStyle(o.status)}>
                      <span className="flex items-center gap-1">
                        {statusIcon(o.status)}
                        {o.status.replace(/_/g, " ")}
                      </span>
                    </Badge>
                  </td>
                  <td
                    className="px-4 py-3 text-xs font-mono"
                    style={{ color: "var(--color-text-tertiary)" }}
                  >
                    {formatRelativeTime(o.submittedAt ?? o.createdAt)}
                  </td>
                  <td
                    className="px-4 py-3 font-mono text-xs"
                    style={{ color: "var(--color-text-tertiary)" }}
                    title={o.exchangeOrderId ?? ""}
                  >
                    {o.exchangeOrderId
                      ? o.exchangeOrderId.slice(0, 10) + "…"
                      : o.clordId.slice(0, 10) + "…"}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {o.status !== "CANCEL_REQUESTED" && (
                      <button
                        onClick={() => setConfirmId(o.id)}
                        className="text-xs font-mono px-3 py-1 rounded-sm transition-colors"
                        style={{
                          color: "var(--color-accent-danger)",
                          background:
                            "color-mix(in srgb, var(--color-accent-danger) 8%, transparent)",
                          border:
                            "1px solid color-mix(in srgb, var(--color-accent-danger) 25%, transparent)",
                        }}
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ── Closed / failed execution blotter ─────────────────────────────────────────

function ClosedBlotter() {
  const { data, isLoading } = useLiveOrders({ status: "closed", limit: 200 });
  const orders = data?.orders ?? [];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left whitespace-nowrap">
        <thead
          className="text-xs font-mono uppercase"
          style={{
            color: "var(--color-text-tertiary)",
            background: "color-mix(in srgb, var(--color-app-surface-hover) 50%, transparent)",
            borderBottom: "1px solid var(--color-app-border)",
          }}
        >
          <tr>
            <th className="px-4 py-3 font-medium">Market</th>
            <th className="px-4 py-3 font-medium">Strategy</th>
            <th className="px-4 py-3 font-medium">Side</th>
            <th className="px-4 py-3 font-medium text-right">Size (USDC)</th>
            <th className="px-4 py-3 font-medium text-right">Entry Price</th>
            <th className="px-4 py-3 font-medium text-right">Fill Price</th>
            <th className="px-4 py-3 font-medium text-right">Filled Qty</th>
            <th className="px-4 py-3 font-medium text-right">Fill %</th>
            <th className="px-4 py-3 font-medium text-center">Status</th>
            <th className="px-4 py-3 font-medium">Submitted</th>
            <th className="px-4 py-3 font-medium">Closed</th>
            <th className="px-4 py-3 font-medium">Error</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-app-border)]/50">
          {isLoading ? (
            <tr>
              <td colSpan={12} className="p-4">
                <TableSkeleton />
              </td>
            </tr>
          ) : orders.length === 0 ? (
            <tr>
              <td
                colSpan={12}
                className="px-4 py-14 text-center font-mono text-sm"
                style={{ color: "var(--color-text-tertiary)" }}
              >
                No closed orders yet
              </td>
            </tr>
          ) : (
            orders.map((o) => (
              <tr
                key={o.id}
                className="transition-colors hover:bg-[var(--color-app-surface-hover)]/40"
              >
                <td
                  className="px-4 py-3 font-medium max-w-[260px] truncate"
                  style={{ color: "var(--color-text-primary)" }}
                  title={o.question ?? o.marketId}
                >
                  {o.question ?? o.marketId}
                </td>
                <td className="px-4 py-3">
                  <Badge className={getStrategyColor(o.strategy)}>
                    {o.strategy.replace("_engine", "").replace(/_/g, "-")}
                  </Badge>
                </td>
                <td className="px-4 py-3">
                  <span
                    className={cn(
                      "text-xs font-mono font-bold px-2 py-0.5 rounded-sm border",
                      o.side === "YES"
                        ? "text-emerald-400 bg-emerald-400/10 border-emerald-400/20"
                        : "text-red-400 bg-red-400/10 border-red-400/20",
                    )}
                  >
                    {o.side}
                  </span>
                </td>
                <td
                  className="px-4 py-3 text-right font-mono"
                  style={{ color: "var(--color-text-primary)" }}
                >
                  ${parseNumeric(o.sizeUsdc).toFixed(2)}
                </td>
                <td
                  className="px-4 py-3 text-right font-mono"
                  style={{ color: "var(--color-accent-primary)" }}
                >
                  {formatPrice(o.price)}
                </td>
                <td
                  className="px-4 py-3 text-right font-mono"
                  style={{ color: "var(--color-text-primary)" }}
                >
                  {o.fillPrice ? formatPrice(o.fillPrice) : "—"}
                </td>
                <td
                  className="px-4 py-3 text-right font-mono"
                  style={{ color: "var(--color-text-secondary)" }}
                >
                  {parseNumeric(o.filledQty).toFixed(2)}
                </td>
                <td
                  className="px-4 py-3 text-right font-mono text-xs"
                  style={{ color: "var(--color-text-secondary)" }}
                >
                  {fillPct(o.filledQty, o.sizeUsdc)}
                </td>
                <td className="px-4 py-3 text-center">
                  <Badge className={statusStyle(o.status)}>
                    <span className="flex items-center gap-1">
                      {statusIcon(o.status)}
                      {o.status.replace(/_/g, " ")}
                    </span>
                  </Badge>
                </td>
                <td
                  className="px-4 py-3 text-xs font-mono"
                  style={{ color: "var(--color-text-tertiary)" }}
                >
                  {formatRelativeTime(o.submittedAt ?? o.createdAt)}
                </td>
                <td
                  className="px-4 py-3 text-xs font-mono"
                  style={{ color: "var(--color-text-tertiary)" }}
                >
                  {formatRelativeTime(closedAt(o))}
                </td>
                <td
                  className="px-4 py-3 text-xs font-mono max-w-[200px] truncate"
                  style={{ color: "var(--color-accent-danger)" }}
                  title={o.errorMsg ?? ""}
                >
                  {o.errorMsg ?? "—"}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── Positions blotter ──────────────────────────────────────────────────────────

function PositionsBlotter() {
  const { data, isLoading } = useLivePositions();
  const positions = data?.positions ?? [];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm text-left whitespace-nowrap">
        <thead
          className="text-xs font-mono uppercase"
          style={{
            color: "var(--color-text-tertiary)",
            background: "color-mix(in srgb, var(--color-app-surface-hover) 50%, transparent)",
            borderBottom: "1px solid var(--color-app-border)",
          }}
        >
          <tr>
            <th className="px-4 py-3 font-medium">Market</th>
            <th className="px-4 py-3 font-medium">Token ID</th>
            <th className="px-4 py-3 font-medium">Side</th>
            <th className="px-4 py-3 font-medium text-right">Bought</th>
            <th className="px-4 py-3 font-medium text-right">Sold</th>
            <th className="px-4 py-3 font-medium text-right">Net Shares</th>
            <th className="px-4 py-3 font-medium text-right">Working Buy</th>
            <th className="px-4 py-3 font-medium text-right">Avg Cost</th>
            <th className="px-4 py-3 font-medium text-right">PnL Open</th>
            <th className="px-4 py-3 font-medium text-right">PnL Realized</th>
            <th className="px-4 py-3 font-medium">Updated</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--color-app-border)]/50">
          {isLoading ? (
            <tr>
              <td colSpan={11} className="p-4">
                <TableSkeleton />
              </td>
            </tr>
          ) : positions.length === 0 ? (
            <tr>
              <td
                colSpan={11}
                className="px-4 py-14 text-center font-mono text-sm"
                style={{ color: "var(--color-text-tertiary)" }}
              >
                No open positions
              </td>
            </tr>
          ) : (
            positions.map((p) => {
              const net =
                parseNumeric(p.totalBought) - parseNumeric(p.totalSold);
              const pnlOpen = parseNumeric(p.pnlOpen);
              const pnlRealized = parseNumeric(p.pnlRealized);
              return (
                <tr
                  key={p.id}
                  className="transition-colors hover:bg-[var(--color-app-surface-hover)]/40"
                >
                  <td
                    className="px-4 py-3 font-medium max-w-[260px] truncate"
                    style={{ color: "var(--color-text-primary)" }}
                    title={p.question ?? p.marketId}
                  >
                    {p.question ?? p.marketId}
                  </td>
                  <td
                    className="px-4 py-3 font-mono text-xs"
                    style={{ color: "var(--color-text-tertiary)" }}
                    title={p.tokenId}
                  >
                    {p.tokenId.slice(0, 10)}…
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={cn(
                        "text-xs font-mono font-bold px-2 py-0.5 rounded-sm border",
                        p.side === "YES"
                          ? "text-emerald-400 bg-emerald-400/10 border-emerald-400/20"
                          : "text-red-400 bg-red-400/10 border-red-400/20",
                      )}
                    >
                      {p.side}
                    </span>
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    {parseNumeric(p.totalBought).toFixed(2)}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    {parseNumeric(p.totalSold).toFixed(2)}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono font-bold"
                    style={{ color: "var(--color-text-primary)" }}
                  >
                    {net.toFixed(2)}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono"
                    style={{ color: "var(--color-text-secondary)" }}
                  >
                    {parseNumeric(p.workingBuy).toFixed(2)}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono"
                    style={{ color: "var(--color-accent-primary)" }}
                  >
                    {p.avgCost ? formatPrice(p.avgCost) : "—"}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono font-bold"
                    style={{
                      color:
                        pnlOpen >= 0
                          ? "var(--color-accent-success)"
                          : "var(--color-accent-danger)",
                    }}
                  >
                    {pnlOpen >= 0 ? "+" : ""}
                    {pnlOpen.toFixed(4)}
                  </td>
                  <td
                    className="px-4 py-3 text-right font-mono font-bold"
                    style={{
                      color:
                        pnlRealized >= 0
                          ? "var(--color-accent-success)"
                          : "var(--color-accent-danger)",
                    }}
                  >
                    {pnlRealized >= 0 ? "+" : ""}
                    {pnlRealized.toFixed(4)}
                  </td>
                  <td
                    className="px-4 py-3 text-xs font-mono"
                    style={{ color: "var(--color-text-tertiary)" }}
                  >
                    {formatRelativeTime(p.lastUpdated)}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

type BlotterTab = "active" | "closed" | "positions";

export default function Execution() {
  const [tab, setTab] = useState<BlotterTab>("active");
  const [showCancelAll, setShowCancelAll] = useState(false);
  const qc = useQueryClient();

  const { data: activeData } = useLiveOrders({ status: "active" });
  const { data: closedData } = useLiveOrders({ status: "closed", limit: 1 });
  const { data: positionsData } = useLivePositions();
  const { data: portfolio } = useLivePortfolio();

  const cancelAllMutation = useCancelAllOrders({
    mutation: {
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ["listOrders"] });
        setShowCancelAll(false);
      },
    },
  });

  const activeCount = activeData?.count ?? 0;
  const closedCount = closedData?.count ?? 0;

  const usdcAtRisk = (activeData?.orders ?? []).reduce(
    (sum, o) => sum + parseNumeric(o.sizeUsdc),
    0,
  );

  const openPositions = positionsData?.count ?? 0;
  const totalPnlOpen = (positionsData?.positions ?? []).reduce(
    (sum, p) => sum + parseNumeric(p.pnlOpen),
    0,
  );

  const TABS: { id: BlotterTab; label: string; count?: number }[] = [
    { id: "active",    label: "Order Blotter",     count: activeCount },
    { id: "closed",    label: "Execution Blotter"  },
    { id: "positions", label: "Open Positions",     count: openPositions },
  ];

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="space-y-6">
      <ConfirmDialog
        open={showCancelAll}
        title="Cancel ALL active orders?"
        body={`This will mark all ${activeCount} active order${activeCount !== 1 ? "s" : ""} as CANCEL_REQUESTED. The executor will send cancel requests to the exchange on its next cycle.`}
        confirmLabel={`Cancel ${activeCount} order${activeCount !== 1 ? "s" : ""}`}
        danger
        loading={cancelAllMutation.isPending}
        onConfirm={() => cancelAllMutation.mutate()}
        onCancel={() => setShowCancelAll(false)}
      />

      {/* ── Header ── */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h2
            className="text-2xl font-bold tracking-tight flex items-center gap-2"
            style={{ color: "var(--color-text-primary)" }}
          >
            <Zap style={{ color: "var(--color-accent-primary)" }} />
            Execution Console
          </h2>
          <p
            className="text-sm font-mono mt-1"
            style={{ color: "var(--color-text-secondary)" }}
          >
            Monitor orders, manage positions, and control the executor
          </p>
        </div>

        <button
          onClick={() => setShowCancelAll(true)}
          disabled={activeCount === 0}
          className="flex items-center gap-2 px-4 py-2 text-sm font-mono rounded-sm transition-colors"
          style={{
            color: activeCount > 0 ? "var(--color-accent-danger)" : "var(--color-text-tertiary)",
            background:
              activeCount > 0
                ? "color-mix(in srgb, var(--color-accent-danger) 10%, transparent)"
                : "var(--color-app-surface-hover)",
            border:
              activeCount > 0
                ? "1px solid color-mix(in srgb, var(--color-accent-danger) 30%, transparent)"
                : "1px solid var(--color-app-border)",
            cursor: activeCount === 0 ? "not-allowed" : "pointer",
          }}
        >
          <Ban className="w-4 h-4" />
          Cancel All Active
          {activeCount > 0 && (
            <span
              className="ml-1 px-1.5 py-0.5 text-[10px] rounded-sm font-bold"
              style={{
                background: "color-mix(in srgb, var(--color-accent-danger) 20%, transparent)",
                color: "var(--color-accent-danger)",
              }}
            >
              {activeCount}
            </span>
          )}
        </button>
      </div>

      {/* ── Stats row ── */}
      <div className="flex flex-wrap gap-3">
        {portfolio && (
          <StatCard
            icon={<DollarSign className="w-4 h-4" />}
            label="Bankroll"
            value={`$${parseNumeric(portfolio.bankroll).toFixed(2)}`}
            sub={
              parseNumeric(portfolio.bankroll) > 0
                ? `${(parseNumeric(portfolio.deployedPct) * 100).toFixed(1)}% deployed · $${parseNumeric(portfolio.available).toFixed(2)} free`
                : "BANKROLL_USDC not set"
            }
          />
        )}
        <StatCard
          icon={<Wallet className="w-4 h-4" />}
          label="USDC at Risk"
          value={`$${usdcAtRisk.toFixed(2)}`}
          sub="active orders"
        />
        <StatCard
          icon={<Clock className="w-4 h-4" />}
          label="Active Orders"
          value={activeCount}
          sub="pending / submitted"
        />
        <StatCard
          icon={<TrendingUp className="w-4 h-4" />}
          label="Open Positions"
          value={openPositions}
          sub={
            openPositions > 0
              ? `PnL ${totalPnlOpen >= 0 ? "+" : ""}${totalPnlOpen.toFixed(4)}`
              : "no exposure"
          }
        />
        <StatCard
          icon={<CheckCircle2 className="w-4 h-4" />}
          label="Closed Orders"
          value={closedCount}
          sub="filled / canceled / failed"
        />
      </div>

      {/* ── Tab bar ── */}
      <div
        className="terminal-panel overflow-hidden"
        style={{ padding: 0 }}
      >
        <div
          className="flex"
          style={{ borderBottom: "1px solid var(--color-app-border)" }}
        >
          {TABS.map(({ id, label, count }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className="px-5 py-3.5 text-sm font-mono transition-colors relative flex items-center gap-2"
              style={{
                color:
                  tab === id
                    ? "var(--color-text-primary)"
                    : "var(--color-text-tertiary)",
                background:
                  tab === id
                    ? "color-mix(in srgb, var(--color-app-surface-hover) 60%, transparent)"
                    : "transparent",
                borderBottom:
                  tab === id
                    ? "2px solid var(--color-accent-primary)"
                    : "2px solid transparent",
              }}
            >
              {label}
              {count !== undefined && count > 0 && (
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-sm font-bold"
                  style={{
                    background: "color-mix(in srgb, var(--color-accent-primary) 15%, transparent)",
                    color: "var(--color-accent-primary)",
                  }}
                >
                  {count}
                </span>
              )}
            </button>
          ))}
        </div>

        {tab === "active" && <ActiveBlotter />}
        {tab === "closed" && <ClosedBlotter />}
        {tab === "positions" && <PositionsBlotter />}
      </div>
    </motion.div>
  );
}
