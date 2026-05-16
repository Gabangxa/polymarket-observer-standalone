import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CirclePause,
  CirclePlay,
  Clock,
  DollarSign,
  KeyRound,
  Loader2,
  Pencil,
  Send,
  TrendingUp,
  Wallet,
  XCircle,
  Zap,
} from "lucide-react";
import { setApiKey } from "@workspace/api-client-react";
import {
  useLiveMarkets,
  useLiveOrders,
  useLivePositions,
  useLivePortfolio,
  useCancelOrder,
  useCancelAllOrders,
  useSetBankroll,
  useSubmitSignal,
  useMarketSignals,
  useExecutorStatus,
  usePauseExecutor,
  useResumeExecutor,
  type ConnectionStatus as ConnectionStatusData,
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

// ── API Key settings ───────────────────────────────────────────────────────────

function ApiKeySettings() {
  const [key, setKey] = useState(() => localStorage.getItem("x-api-key") ?? "");
  const [saved, setSaved] = useState(false);

  function save() {
    const trimmed = key.trim();
    localStorage.setItem("x-api-key", trimmed);
    setApiKey(trimmed || null);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  return (
    <div className="flex items-center gap-3 flex-wrap">
      <KeyRound className="w-4 h-4 shrink-0" style={{ color: "var(--color-text-tertiary)" }} />
      <span className="text-xs font-mono" style={{ color: "var(--color-text-tertiary)" }}>API Key</span>
      <input
        type="password"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && save()}
        placeholder="INTERNAL_API_KEY"
        className="flex-1 min-w-48 text-xs font-mono px-3 py-1.5 rounded-sm bg-transparent border outline-none"
        style={{
          borderColor: "var(--color-app-border)",
          color: "var(--color-text-primary)",
        }}
      />
      <button
        onClick={save}
        className="text-xs font-mono px-3 py-1.5 rounded-sm transition-colors"
        style={{
          color: saved ? "var(--color-accent-success)" : "var(--color-accent-primary)",
          background: "color-mix(in srgb, var(--color-accent-primary) 10%, transparent)",
          border: "1px solid color-mix(in srgb, var(--color-accent-primary) 25%, transparent)",
        }}
      >
        {saved ? "Saved" : "Save"}
      </button>
    </div>
  );
}

// ── Manual signal form ─────────────────────────────────────────────────────────

const STRATEGIES = ["spread_engine", "tail_yield_engine", "neg_risk_overround"] as const;

function ManualSignalPanel() {
  const [open, setOpen] = useState(false);
  const { data: marketsData } = useLiveMarkets();
  const submitSignal = useSubmitSignal();

  const [search, setSearch] = useState("");
  const [marketId, setMarketId] = useState("");
  const [strategy, setStrategy] = useState<string>(STRATEGIES[0]);
  const [score, setScore] = useState("0.85");
  const [kelly, setKelly] = useState("0.01");
  const [success, setSuccess] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: signalData } = useMarketSignals(marketId);
  const latestSignal = signalData?.signals?.[0] ?? null;
  const latestScore = latestSignal ? parseNumeric(latestSignal.signalScore) : null;

  const markets = (marketsData?.markets ?? []).filter((m) =>
    !search || m.question?.toLowerCase().includes(search.toLowerCase()),
  );

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    const scoreNum = parseFloat(score);
    if (isNaN(scoreNum) || scoreNum < 0.75) {
      setError("Signal score must be ≥ 0.75");
      return;
    }
    submitSignal.mutate(
      {
        strategy,
        marketId,
        signalScore: score,
        metadata: { kelly_fraction: parseFloat(kelly), source: "manual" },
      },
      {
        onSuccess: (data) => { setSuccess(data.id); setMarketId(""); setSearch(""); },
        onError: (err) => setError((err as Error).message),
      },
    );
  }

  const inputStyle = {
    background: "var(--color-app-surface-hover)",
    border: "1px solid var(--color-app-border)",
    color: "var(--color-text-primary)",
  };

  return (
    <div className="terminal-panel overflow-hidden" style={{ padding: 0 }}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-5 py-3.5 text-sm font-mono transition-colors"
        style={{ color: "var(--color-text-secondary)", borderBottom: open ? "1px solid var(--color-app-border)" : "none" }}
      >
        <span className="flex items-center gap-2">
          <Send className="w-4 h-4" style={{ color: "var(--color-accent-primary)" }} />
          Manual Execution
        </span>
        {open ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
      </button>

      {open && (
        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {/* Market search + select */}
          <div className="space-y-1.5">
            <label className="text-xs font-mono" style={{ color: "var(--color-text-tertiary)" }}>
              Market
            </label>
            <input
              type="text"
              placeholder="Search markets…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setMarketId(""); }}
              className="w-full text-sm font-mono px-3 py-2 rounded-sm outline-none"
              style={inputStyle}
            />
            {search && markets.length > 0 && !marketId && (
              <div
                className="rounded-sm border max-h-48 overflow-y-auto"
                style={{ background: "var(--color-app-surface)", borderColor: "var(--color-app-border)" }}
              >
                {markets.slice(0, 12).map((m) => (
                  <button
                    key={m.marketId}
                    type="button"
                    onClick={() => { setMarketId(m.marketId); setSearch(m.question ?? m.marketId); }}
                    className="w-full text-left px-3 py-2 text-xs font-mono transition-colors hover:bg-[var(--color-app-surface-hover)]"
                    style={{ color: "var(--color-text-primary)", borderBottom: "1px solid var(--color-app-border)" }}
                  >
                    <div className="truncate">{m.question ?? m.marketId}</div>
                    <div className="text-[10px] mt-0.5" style={{ color: "var(--color-text-tertiary)" }}>{m.marketId.slice(0, 24)}…</div>
                  </button>
                ))}
              </div>
            )}
            {marketId && (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[10px] font-mono" style={{ color: "var(--color-accent-primary)" }}>
                  {marketId.slice(0, 32)}…
                </span>
                {latestScore !== null ? (
                  <span
                    className="text-[10px] font-mono font-bold px-1.5 py-0.5 rounded-sm border"
                    style={
                      latestScore >= 0.75
                        ? { color: "var(--color-accent-success)", background: "color-mix(in srgb, var(--color-accent-success) 10%, transparent)", borderColor: "color-mix(in srgb, var(--color-accent-success) 30%, transparent)" }
                        : { color: "var(--color-accent-danger)", background: "color-mix(in srgb, var(--color-accent-danger) 10%, transparent)", borderColor: "color-mix(in srgb, var(--color-accent-danger) 30%, transparent)" }
                    }
                  >
                    latest signal: {latestScore.toFixed(3)} {latestScore < 0.75 ? "⚠ below threshold" : ""}
                  </span>
                ) : signalData !== undefined ? (
                  <span className="text-[10px] font-mono" style={{ color: "var(--color-text-tertiary)" }}>
                    no recent signal
                  </span>
                ) : null}
              </div>
            )}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {/* Strategy */}
            <div className="space-y-1.5">
              <label className="text-xs font-mono" style={{ color: "var(--color-text-tertiary)" }}>Strategy</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                className="w-full text-sm font-mono px-3 py-2 rounded-sm outline-none"
                style={inputStyle}
              >
                {STRATEGIES.map((s) => (
                  <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
                ))}
              </select>
            </div>

            {/* Signal score */}
            <div className="space-y-1.5">
              <label className="text-xs font-mono" style={{ color: "var(--color-text-tertiary)" }}>
                Signal Score <span style={{ color: "var(--color-text-tertiary)" }}>(min 0.75)</span>
              </label>
              <input
                type="number"
                min="0.75"
                max="1"
                step="0.01"
                value={score}
                onChange={(e) => setScore(e.target.value)}
                className="w-full text-sm font-mono px-3 py-2 rounded-sm outline-none"
                style={inputStyle}
              />
            </div>

            {/* Kelly fraction */}
            <div className="space-y-1.5">
              <label className="text-xs font-mono" style={{ color: "var(--color-text-tertiary)" }}>
                Kelly Fraction <span style={{ color: "var(--color-text-tertiary)" }}>(e.g. 0.01 = 1%)</span>
              </label>
              <input
                type="number"
                min="0.001"
                max="0.25"
                step="0.001"
                value={kelly}
                onChange={(e) => setKelly(e.target.value)}
                className="w-full text-sm font-mono px-3 py-2 rounded-sm outline-none"
                style={inputStyle}
              />
            </div>
          </div>

          {/* Feedback */}
          {error && (
            <div className="text-xs font-mono px-3 py-2 rounded-sm" style={{ color: "var(--color-accent-danger)", background: "color-mix(in srgb, var(--color-accent-danger) 10%, transparent)" }}>
              {error}
            </div>
          )}
          {success !== null && (
            <div className="text-xs font-mono px-3 py-2 rounded-sm" style={{ color: "var(--color-accent-success)", background: "color-mix(in srgb, var(--color-accent-success) 10%, transparent)" }}>
              Signal submitted — id {success === -1 ? "(deduped, already exists this hour)" : `#${success}`}. Executor picks it up within 10s.
            </div>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={!marketId || submitSignal.isPending}
              className="flex items-center gap-2 px-5 py-2 text-sm font-mono rounded-sm transition-colors"
              style={{
                color: "var(--color-accent-primary)",
                background: "color-mix(in srgb, var(--color-accent-primary) 15%, transparent)",
                border: "1px solid color-mix(in srgb, var(--color-accent-primary) 30%, transparent)",
                opacity: (!marketId || submitSignal.isPending) ? 0.5 : 1,
                cursor: (!marketId || submitSignal.isPending) ? "not-allowed" : "pointer",
              }}
            >
              {submitSignal.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              Submit Signal
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

// ── Connection probe indicators ────────────────────────────────────────────────

function ConnDot({
  ok,
  label,
  unknown,
}: {
  ok: boolean;
  label: string;
  unknown?: boolean;
}) {
  const color = unknown
    ? "var(--color-text-tertiary)"
    : ok
    ? "var(--color-accent-success)"
    : "var(--color-accent-danger)";

  return (
    <div className="flex items-center gap-1" title={label}>
      <span
        className={cn("w-1.5 h-1.5 rounded-full", !unknown && !ok && "animate-pulse")}
        style={{ background: color }}
      />
      <span className="text-[10px] font-mono" style={{ color }}>
        {label}
      </span>
    </div>
  );
}

function ConnStatus({ conn }: { conn: ConnectionStatusData | null | undefined }) {
  if (conn === undefined) return null;

  const unknown = conn === null;
  const age = conn?.checkedAt
    ? Math.floor((Date.now() - new Date(conn.checkedAt).getTime()) / 1000)
    : null;

  return (
    <div
      className="flex items-center gap-3 px-3 py-1.5 rounded-sm"
      style={{
        background: "var(--color-app-surface-hover)",
        border: "1px solid var(--color-app-border)",
      }}
      title={
        conn?.error
          ? `Error: ${conn.error}`
          : age !== null
          ? `Last checked ${age}s ago`
          : "No check run yet"
      }
    >
      <ConnDot ok={conn?.clobReachable ?? false} label="CLOB" unknown={unknown} />
      <ConnDot ok={conn?.walletAuthenticated ?? false} label="Wallet" unknown={unknown} />
      {age !== null && (
        <span className="text-[10px] font-mono" style={{ color: "var(--color-text-tertiary)" }}>
          {age < 60 ? `${age}s ago` : `${Math.floor(age / 60)}m ago`}
        </span>
      )}
    </div>
  );
}

// ── Executor toggle ────────────────────────────────────────────────────────────

function ExecutorToggle() {
  const { data: status } = useExecutorStatus();
  const pause = usePauseExecutor();
  const resume = useResumeExecutor();
  const [confirmPause, setConfirmPause] = useState(false);

  const paused = status?.paused ?? null;
  const loading = pause.isPending || resume.isPending;

  if (paused === null) return null;

  return (
    <>
      <ConfirmDialog
        open={confirmPause}
        title="Pause the executor?"
        body="Signal processing will halt. Fill polling and order tracking continue. Resume at any time from this page."
        confirmLabel="Pause Executor"
        danger
        loading={pause.isPending}
        onConfirm={() => {
          pause.mutate(undefined, { onSettled: () => setConfirmPause(false) });
        }}
        onCancel={() => setConfirmPause(false)}
      />

      <ConnStatus conn={status?.connection} />

      <span
        className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-sm border ${
          paused
            ? "text-amber-400 bg-amber-400/10 border-amber-400/30 animate-pulse"
            : "text-emerald-400 bg-emerald-400/10 border-emerald-400/30"
        }`}
      >
        {paused ? "PAUSED" : "RUNNING"}
      </span>

      <button
        disabled={loading}
        onClick={() => {
          if (paused) {
            resume.mutate();
          } else {
            setConfirmPause(true);
          }
        }}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono rounded-sm transition-colors"
        style={{
          color: paused ? "var(--color-accent-success)" : "var(--color-accent-warning, #f59e0b)",
          background: paused
            ? "color-mix(in srgb, var(--color-accent-success) 10%, transparent)"
            : "color-mix(in srgb, #f59e0b 10%, transparent)",
          border: paused
            ? "1px solid color-mix(in srgb, var(--color-accent-success) 30%, transparent)"
            : "1px solid color-mix(in srgb, #f59e0b 30%, transparent)",
          opacity: loading ? 0.5 : 1,
          cursor: loading ? "not-allowed" : "pointer",
        }}
      >
        {loading ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : paused ? (
          <CirclePlay className="w-3.5 h-3.5" />
        ) : (
          <CirclePause className="w-3.5 h-3.5" />
        )}
        {paused ? "Resume" : "Pause"} Executor
      </button>
    </>
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
  const setBankroll = useSetBankroll();
  const [editingBankroll, setEditingBankroll] = useState(false);
  const [bankrollInput, setBankrollInput] = useState("");
  const bankrollInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingBankroll) {
      setBankrollInput(String(parseNumeric(portfolio?.bankroll)));
      bankrollInputRef.current?.focus();
      bankrollInputRef.current?.select();
    }
  }, [editingBankroll, portfolio?.bankroll]);

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
      {/* ── Settings row ── */}
      <div className="terminal-panel px-5 py-3">
        <ApiKeySettings />
      </div>

      <ManualSignalPanel />

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

        <div className="flex items-center gap-2 flex-wrap">
          <ExecutorToggle />

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
      </div>

      {/* ── Stats row ── */}
      <div className="flex flex-wrap gap-3">
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
            <DollarSign className="w-4 h-4" style={{ color: "var(--color-accent-primary)" }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-xs font-mono flex items-center gap-2" style={{ color: "var(--color-text-tertiary)" }}>
              Bankroll
              <button
                onClick={() => setEditingBankroll(true)}
                className="opacity-50 hover:opacity-100 transition-opacity"
                title="Edit bankroll"
              >
                <Pencil className="w-3 h-3" />
              </button>
            </div>
            {editingBankroll ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  const val = parseFloat(bankrollInput);
                  if (!isNaN(val) && val >= 0) {
                    setBankroll.mutate(val, { onSettled: () => setEditingBankroll(false) });
                  }
                }}
                className="flex items-center gap-1 mt-0.5"
              >
                <span className="text-lg font-bold font-mono" style={{ color: "var(--color-text-primary)" }}>$</span>
                <input
                  ref={bankrollInputRef}
                  type="number"
                  min="0"
                  step="any"
                  value={bankrollInput}
                  onChange={(e) => setBankrollInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Escape" && setEditingBankroll(false)}
                  className="w-28 text-lg font-bold font-mono bg-transparent border-b outline-none"
                  style={{
                    color: "var(--color-text-primary)",
                    borderColor: "var(--color-accent-primary)",
                  }}
                />
                <button
                  type="submit"
                  disabled={setBankroll.isPending}
                  className="text-xs font-mono px-2 py-0.5 rounded-sm"
                  style={{
                    color: "var(--color-accent-primary)",
                    background: "color-mix(in srgb, var(--color-accent-primary) 15%, transparent)",
                    border: "1px solid var(--color-accent-primary)",
                  }}
                >
                  {setBankroll.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : "Save"}
                </button>
                <button
                  type="button"
                  onClick={() => setEditingBankroll(false)}
                  className="text-xs font-mono px-2 py-0.5 rounded-sm"
                  style={{ color: "var(--color-text-tertiary)" }}
                >
                  ✕
                </button>
              </form>
            ) : (
              <div className="text-lg font-bold font-mono" style={{ color: "var(--color-text-primary)" }}>
                ${parseNumeric(portfolio?.bankroll).toFixed(2)}
              </div>
            )}
            <div className="text-[10px] font-mono" style={{ color: "var(--color-text-tertiary)" }}>
              {parseNumeric(portfolio?.bankroll) > 0
                ? `${(parseNumeric(portfolio?.deployedPct) * 100).toFixed(1)}% deployed · $${parseNumeric(portfolio?.available).toFixed(2)} free`
                : "click ✎ to set bankroll"}
            </div>
          </div>
        </div>
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
