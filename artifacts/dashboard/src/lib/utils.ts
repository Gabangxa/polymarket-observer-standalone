import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { formatDistanceToNow, parseISO, format } from "date-fns";
import { TZDate } from "@date-fns/tz";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function parseNumeric(val: string | number | null | undefined): number {
  if (val === null || val === undefined) return 0;
  if (typeof val === 'number') return val;
  const parsed = parseFloat(val);
  return isNaN(parsed) ? 0 : parsed;
}

export function formatCurrency(val: string | number | null | undefined): string {
  const num = parseNumeric(val);
  if (num === 0) return "-";
  
  if (num >= 1_000_000) {
    return `$${(num / 1_000_000).toFixed(2)}M`;
  }
  if (num >= 1_000) {
    return `$${(num / 1_000).toFixed(2)}k`;
  }
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(num);
}

export function formatPrice(val: string | number | null | undefined): string {
  const num = parseNumeric(val);
  return `$${num.toFixed(3)}`;
}

export function formatPercent(val: string | number | null | undefined): string {
  const num = parseNumeric(val);
  return `${(num * 100).toFixed(2)}%`;
}

export function formatRelativeTime(dateStr: string | null | undefined): string {
  if (!dateStr) return "-";
  try {
    return formatDistanceToNow(parseISO(dateStr), { addSuffix: true });
  } catch (e) {
    return dateStr;
  }
}

export function formatInTz(
  dateInput: string | Date | null | undefined,
  pattern: string,
  tz: string
): string {
  if (!dateInput) return "-";
  try {
    const d = typeof dateInput === "string" ? parseISO(dateInput) : dateInput;
    return format(new TZDate(d, tz), pattern);
  } catch {
    return "-";
  }
}

export function getStrategyColor(strategy: string): string {
  if (strategy.includes("spread")) return "text-blue-400 bg-blue-400/10 border-blue-400/20";
  if (strategy.includes("neg_risk")) return "text-purple-400 bg-purple-400/10 border-purple-400/20";
  if (strategy.includes("reversion")) return "text-amber-400 bg-amber-400/10 border-amber-400/20";
  return "text-slate-400 bg-slate-400/10 border-slate-400/20";
}
