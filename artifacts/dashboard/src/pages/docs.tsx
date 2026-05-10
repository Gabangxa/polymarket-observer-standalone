import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  BookOpen,
  ChevronDown,
  LayoutDashboard,
  BarChart2,
  Activity,
  TrendingUp,
  Database,
  Zap,
  Target,
  Shuffle,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  ArrowRight,
} from "lucide-react";
import { Link } from "wouter";
import { Badge } from "@/components/ui-elements";

// ── FAQ accordion ─────────────────────────────────────────────────────────────

function FaqItem({ q, children }: { q: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-border rounded-md overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-4 text-left text-sm font-medium hover:bg-accent/50 transition-colors"
      >
        <span>{q}</span>
        <ChevronDown
          size={16}
          className={`shrink-0 text-muted-foreground transition-transform duration-200 ${open ? "rotate-180" : ""}`}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="px-5 pb-5 pt-1 text-sm text-muted-foreground leading-relaxed border-t border-border">
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Section heading ───────────────────────────────────────────────────────────

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-lg font-bold tracking-tight flex items-center gap-2 border-b border-border pb-3 mb-5">
      {children}
    </h2>
  );
}

// ── Pill ──────────────────────────────────────────────────────────────────────

function Pill({ children, color = "default" }: { children: React.ReactNode; color?: "green" | "red" | "blue" | "default" }) {
  const cls = {
    green:   "bg-success/10 text-success border-success/20",
    red:     "bg-destructive/10 text-destructive border-destructive/20",
    blue:    "bg-primary/10 text-primary border-primary/20",
    default: "bg-muted text-muted-foreground border-border",
  }[color];
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono border ${cls}`}>
      {children}
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function Docs() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-12 pb-16 max-w-4xl"
    >
      {/* Hero */}
      <div className="terminal-panel p-8 border-l-4 border-l-primary">
        <div className="flex items-center gap-3 mb-4">
          <BookOpen className="text-primary" size={28} />
          <h1 className="text-3xl font-bold tracking-tight">Dashboard Guide</h1>
        </div>
        <p className="text-muted-foreground leading-relaxed max-w-2xl">
          PolyBot is an autonomous market analysis system for{" "}
          <span className="text-foreground font-medium">Polymarket</span> — a real-money prediction
          market platform. This dashboard lets you observe the bot's activity, understand what
          opportunities it's detecting, and track how well its signal logic performs over time.
        </p>
        <p className="text-muted-foreground leading-relaxed max-w-2xl mt-3">
          <span className="text-foreground font-medium">Nothing here executes trades automatically.</span>{" "}
          All signals are paper-traded and tracked for outcome — giving you a verified track record
          before any real capital is involved.
        </p>
      </div>

      {/* How it works */}
      <div>
        <SectionHeading><Shuffle size={18} className="text-primary" /> How the system works</SectionHeading>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[
            {
              step: "1",
              title: "Scan & watch",
              body: "Every hour the bot scans 6,000+ active Polymarket markets and builds a watchlist of the top markets by volume and liquidity.",
              icon: Database,
            },
            {
              step: "2",
              title: "Collect & analyse",
              body: "Every 5 minutes it snapshots each watched market — prices, spread, open interest, price history — and runs six strategy engines against the data.",
              icon: Activity,
            },
            {
              step: "3",
              title: "Signal & resolve",
              body: "When a strategy detects an opportunity it emits a signal. After a resolution window (2–6 hours) the bot checks whether the signal was correct and records the outcome.",
              icon: TrendingUp,
            },
          ].map(({ step, title, body, icon: Icon }) => (
            <div key={step} className="terminal-panel p-5">
              <div className="flex items-center gap-3 mb-3">
                <div className="w-7 h-7 rounded-full bg-primary/20 border border-primary/30 flex items-center justify-center text-primary text-xs font-bold font-mono">
                  {step}
                </div>
                <Icon size={16} className="text-primary" />
                <span className="font-semibold text-sm">{title}</span>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Pages guide */}
      <div>
        <SectionHeading><LayoutDashboard size={18} className="text-primary" /> What each page shows</SectionHeading>
        <div className="space-y-3">
          {[
            {
              href: "/",
              icon: LayoutDashboard,
              label: "Overview",
              color: "text-primary",
              summary: "Your command centre.",
              detail:
                "Shows total monitored markets, snapshots collected, signals generated all-time, and 24h signal activity. The strategy breakdown bar shows which engines are most active. The live action feed lists the most recent signals with their market question and score — click any row to drill into that market.",
            },
            {
              href: "/markets",
              icon: BarChart2,
              label: "Markets",
              color: "text-blue-400",
              summary: "The watchlist — markets the bot is actively monitoring.",
              detail:
                "Sorted by score (a composite of volume, liquidity, and hours to close). Neg Risk badge means the market is part of a multi-outcome event eligible for over-round arbitrage. Click any row to open the market detail view.",
            },
            {
              href: "/signals",
              icon: Activity,
              label: "Signals",
              color: "text-yellow-400",
              summary: "Every trade opportunity the engines have flagged in the last 24 hours.",
              detail:
                'Filter by strategy using the pill buttons at the top. Score is the engine\'s confidence metric (higher = stronger opportunity). Entry price is the YES price at signal time. Status shows Active (signal still open) or Resolved (outcome has been computed). Click the market name to see its price history.',
            },
            {
              href: "/performance",
              icon: TrendingUp,
              label: "Performance",
              color: "text-success",
              summary: "Did the signals actually work?",
              detail:
                "Scorecard shows win rate and average PnL per strategy across all resolved signals — in real USDC amounts based on your configured bet size. Use the bet size input (top-right of the page) to set your notional stake per signal; the Est. Total P&L card updates instantly. The accuracy trend bar chart shows daily win rates — green bars are days above 50%, red below. The category breakdown shows which market types (politics, crypto, sports…) each strategy performs best on.",
            },
            {
              href: "/snapshots",
              icon: Database,
              label: "Data Feed",
              color: "text-muted-foreground",
              summary: "Raw, unfiltered data — the latest price snapshot per market.",
              detail:
                "Updated every 5 minutes. YES Ask is the current price to buy a YES share. NO Ask is the price to buy a NO share. Spread is the gap between them. An error badge means the data collection for that market failed on the last run.",
            },
          ].map(({ href, icon: Icon, label, color, summary, detail }) => (
            <div key={href} className="terminal-panel p-5">
              <div className="flex items-start gap-4">
                <div className={`mt-0.5 shrink-0 ${color}`}>
                  <Icon size={20} />
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-semibold">{label}</span>
                    <Link href={href}>
                      <span className="text-xs font-mono text-primary hover:underline cursor-pointer flex items-center gap-1">
                        Open <ArrowRight size={10} />
                      </span>
                    </Link>
                  </div>
                  <p className="text-sm text-foreground font-medium mb-1">{summary}</p>
                  <p className="text-sm text-muted-foreground leading-relaxed">{detail}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Strategies */}
      <div>
        <SectionHeading><Zap size={18} className="text-primary" /> Strategy engines</SectionHeading>
        <div className="space-y-4">

          <div className="terminal-panel p-6 border-l-4 border-l-yellow-500/60">
            <div className="flex items-center gap-2 mb-3">
              <Badge className="bg-yellow-500/10 text-yellow-400 border-yellow-500/20">spread_harvesting</Badge>
              <span className="text-xs font-mono text-muted-foreground">Resolution window: 2 hours</span>
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed mb-3">
              On every prediction market there's a <span className="text-foreground">bid-ask spread</span> — the gap between the cheapest YES you can buy and the cheapest NO you can buy. If that gap is large enough to cover round-trip trading fees with profit left over, you can act as a market maker: post both sides and collect the spread when orders fill.
            </p>
            <div className="bg-muted/30 rounded p-3 font-mono text-xs space-y-1">
              <div className="text-muted-foreground">Signal fires when:</div>
              <div><span className="text-success">spread</span> &gt; <span className="text-primary">SPREAD_FEE_MULTIPLE</span> × (2 × estimated_fee)</div>
              <div className="text-muted-foreground mt-2">Score = (spread − round_trip_fee) / spread</div>
              <div className="text-muted-foreground">Higher score = larger fraction of spread is pure profit</div>
            </div>
            <p className="text-sm text-muted-foreground mt-3">
              <span className="text-foreground font-medium">In plain terms:</span> if YES costs $0.40 and NO costs $0.50, those two together cost $0.90 for a guaranteed $1 payout — a 10¢ edge before fees.
            </p>
          </div>

          <div className="terminal-panel p-6 border-l-4 border-l-purple-500/60">
            <div className="flex items-center gap-2 mb-3">
              <Badge className="bg-purple-500/10 text-purple-400 border-purple-500/20">neg_risk_overround</Badge>
              <span className="text-xs font-mono text-muted-foreground">Resolution window: 6 hours</span>
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed mb-3">
              On a mutually exclusive multi-outcome event (e.g. "Who wins the election: A, B, or C?"), the YES prices of all outcomes must sum to exactly 1.0 at fair value — exactly one will resolve YES. If the sum exceeds 1.0, the market is <span className="text-foreground">over-round</span>: you can sell NO on every outcome and collect a risk-free edge regardless of which resolves.
            </p>
            <div className="bg-muted/30 rounded p-3 font-mono text-xs space-y-1">
              <div className="text-muted-foreground">Signal fires when:</div>
              <div><span className="text-success">sum(yes_prices)</span> &gt; <span className="text-primary">1.0</span></div>
              <div className="text-muted-foreground mt-2">Score = overround (e.g. 0.05 = 5 basis points edge)</div>
            </div>
            <p className="text-sm text-muted-foreground mt-3">
              <span className="text-foreground font-medium">In plain terms:</span> if Outcome A = $0.45, B = $0.40, C = $0.25, the sum is $1.10. Selling NO on all three pays $0.10 guaranteed, regardless of who wins.
            </p>
          </div>

          <div className="terminal-panel p-6 border-l-4 border-l-blue-500/60">
            <div className="flex items-center gap-2 mb-3">
              <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20">mean_reversion</Badge>
              <span className="text-xs font-mono text-muted-foreground">Resolution window: 4 hours</span>
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed mb-3">
              In thin markets with low open interest, a single large order can move the price sharply without any new real-world information. These dislocations tend to partially reverse. The engine flags markets where the price moved more than a threshold within a rolling window, penalised by open interest (high OI = real move, not noise).
            </p>
            <div className="bg-muted/30 rounded p-3 font-mono text-xs space-y-1">
              <div className="text-muted-foreground">Signal fires when:</div>
              <div>
                <span className="text-success">|end_price − start_price|</span> &gt;{" "}
                <span className="text-primary">REVERSION_PRICE_MOVE_THRESHOLD</span>
              </div>
              <div className="text-muted-foreground mt-2">Score = delta × (1 − OI_penalty × 0.5)</div>
              <div className="text-muted-foreground">Direction stored in metadata (up/down)</div>
            </div>
            <p className="text-sm text-muted-foreground mt-3">
              <span className="text-foreground font-medium">In plain terms:</span> if a $50k market swings from $0.30 to $0.65 in two hours for no apparent reason, it's likely noise — bet on it returning toward $0.30.
            </p>
          </div>

          <div className="terminal-panel p-6 border-l-4 border-l-green-500/60">
            <div className="flex items-center gap-2 mb-3">
              <Badge className="bg-green-500/10 text-green-400 border-green-500/20">micro_spread_scalp</Badge>
              <span className="text-xs font-mono text-muted-foreground">Resolution window: 1 hour</span>
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed mb-3">
              When a market's bid-ask spread is wide enough to be exploited as a market maker, this engine models the optimal placement for both sides. Unlike the spread harvesting engine which focuses on fee coverage, micro-spread targets markets where the spread itself is large relative to typical tick size — creating short-term scalping opportunities.
            </p>
            <div className="bg-muted/30 rounded p-3 font-mono text-xs space-y-1">
              <div className="text-muted-foreground">Signal fires when:</div>
              <div><span className="text-success">spread</span> ≥ <span className="text-primary">MICRO_SPREAD_THRESHOLD (0.04)</span></div>
              <div className="text-muted-foreground mt-2">Score = spread / 0.10 (capped at 1.0)</div>
              <div className="text-muted-foreground">Metadata: best_bid, best_ask, optimal_bid, optimal_ask, potential_capture</div>
            </div>
            <p className="text-sm text-muted-foreground mt-3">
              <span className="text-foreground font-medium">Expected frequency:</span> moderate — fires whenever the spread crosses 4¢, which happens regularly in active markets.
            </p>
          </div>

          <div className="terminal-panel p-6 border-l-4 border-l-orange-500/60">
            <div className="flex items-center gap-2 mb-3">
              <Badge className="bg-orange-500/10 text-orange-400 border-orange-500/20">tail_yield_harvest</Badge>
              <span className="text-xs font-mono text-muted-foreground">Resolution window: at expiry</span>
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed mb-3">
              Near-certain outcomes approaching expiry are underpriced relative to their implied yield. When a YES price is ≥ 95¢ but less than 99¢ and the market closes within 48 hours, buying YES and holding to resolution provides a risk-adjusted yield comparable to short-term fixed income — but in a binary contract form.
            </p>
            <div className="bg-muted/30 rounded p-3 font-mono text-xs space-y-1">
              <div className="text-muted-foreground">Signal fires when:</div>
              <div><span className="text-success">yes_price</span> ≥ <span className="text-primary">YIELD_MIN_PRICE (0.95)</span> AND yes_price &lt; 0.99</div>
              <div><span className="text-success">hours_to_close</span> ≤ <span className="text-primary">YIELD_HOURS_TO_EXPIRY (48)</span></div>
              <div className="text-muted-foreground mt-2">yield_pct = ((1 / yes_price) − 1) × 100</div>
              <div className="text-muted-foreground">Score = yield_pct / 5.0 (5% yield = 1.0 score)</div>
              <div className="text-muted-foreground">Metadata: hours_remaining, current_price, yield_percentage</div>
            </div>
            <p className="text-sm text-muted-foreground mt-3">
              <span className="text-foreground font-medium">Expected frequency:</span> moderate — depends on how many markets are in the final 48 hours with high YES prices.
            </p>
          </div>

          <div className="terminal-panel p-6 border-l-4 border-l-red-500/60">
            <div className="flex items-center gap-2 mb-3">
              <Badge className="bg-red-500/10 text-red-400 border-red-500/20">binary_arb</Badge>
              <span className="text-xs font-mono text-muted-foreground">Resolution window: immediate</span>
            </div>
            <p className="text-sm text-muted-foreground leading-relaxed mb-3">
              On a binary market, YES and NO are complementary: exactly one resolves to $1. If you can buy both YES and NO for a combined cost below $1, you lock in a guaranteed profit regardless of outcome. This engine monitors ask prices for both tokens and fires when their sum falls below the arb threshold.
            </p>
            <div className="bg-muted/30 rounded p-3 font-mono text-xs space-y-1">
              <div className="text-muted-foreground">Signal fires when:</div>
              <div><span className="text-success">yes_ask + no_ask</span> &lt; <span className="text-primary">ARB_THRESHOLD (0.98)</span></div>
              <div className="text-muted-foreground mt-2">guaranteed_profit = 1.00 − (yes_ask + no_ask)</div>
              <div className="text-muted-foreground">Score = guaranteed_profit / 0.05 (5¢ profit = 1.0 score)</div>
              <div className="text-muted-foreground">Metadata: buy_yes_at, buy_no_at, total_cost, guaranteed_profit</div>
            </div>
            <p className="text-sm text-muted-foreground mt-3">
              <span className="text-foreground font-medium">Expected frequency:</span> rare — genuine binary arb is quickly eliminated by market participants. Signals here likely reflect data staleness or thin liquidity rather than exploitable opportunities.
            </p>
          </div>

        </div>
      </div>

      {/* Signal field glossary */}
      <div>
        <SectionHeading><Target size={18} className="text-primary" /> Reading a signal — field by field</SectionHeading>
        <div className="terminal-panel overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs font-mono text-muted-foreground uppercase bg-muted/30 border-b border-border">
              <tr>
                <th className="px-4 py-3 font-medium text-left">Field</th>
                <th className="px-4 py-3 font-medium text-left">What it means</th>
                <th className="px-4 py-3 font-medium text-left">Example</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50 text-muted-foreground">
              <tr>
                <td className="px-4 py-3 font-mono text-xs text-foreground whitespace-nowrap">Strategy</td>
                <td className="px-4 py-3">Which engine detected the opportunity</td>
                <td className="px-4 py-3"><Badge className="bg-yellow-500/10 text-yellow-400 border-yellow-500/20">spread_harvesting</Badge></td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-mono text-xs text-foreground whitespace-nowrap">Score</td>
                <td className="px-4 py-3">Confidence metric — higher means stronger opportunity. Scale depends on strategy (0–1 for spread and reversion, basis points for neg-risk)</td>
                <td className="px-4 py-3 font-mono text-primary">0.72</td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-mono text-xs text-foreground whitespace-nowrap">Entry Price</td>
                <td className="px-4 py-3">The YES price at the moment the signal fired — the price you'd have entered at</td>
                <td className="px-4 py-3 font-mono">$0.43</td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-mono text-xs text-foreground whitespace-nowrap">Exit Price</td>
                <td className="px-4 py-3">The YES price when the resolution window expired. Null until resolved.</td>
                <td className="px-4 py-3 font-mono text-muted-foreground">$0.38</td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-mono text-xs text-foreground whitespace-nowrap">PnL</td>
                <td className="px-4 py-3">Paper profit/loss per share. Positive = win, negative = loss. Units are in price (¢ per $1 contract).</td>
                <td className="px-4 py-3">
                  <span className="font-mono text-success">+0.0312</span>
                </td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-mono text-xs text-foreground whitespace-nowrap">Status</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2 mb-1">
                    <Pill color="blue">Active</Pill>
                    <span>— resolution window hasn't expired yet</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Pill>Resolved</Pill>
                    <span>— outcome has been computed and stored</span>
                  </div>
                </td>
                <td className="px-4 py-3"></td>
              </tr>
              <tr>
                <td className="px-4 py-3 font-mono text-xs text-foreground whitespace-nowrap">Outcome</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2 mb-1">
                    <CheckCircle2 size={13} className="text-success" />
                    <span className="text-success font-medium">WIN</span>
                    <span>— signal logic was correct (price behaved as predicted)</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <XCircle size={13} className="text-destructive" />
                    <span className="text-destructive font-medium">LOSS</span>
                    <span>— price moved against the predicted direction</span>
                  </div>
                </td>
                <td className="px-4 py-3"></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Performance metrics */}
      <div>
        <SectionHeading><TrendingUp size={18} className="text-primary" /> Reading performance metrics</SectionHeading>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {[
            {
              term: "Win Rate",
              def: "wins ÷ resolved signals. A rate above 50% means the strategy is directionally correct more often than not. This is necessary but not sufficient — you also need positive average PnL.",
              note: "≥60% is strong. 40–60% is normal. <40% suggests the signal logic needs tuning.",
            },
            {
              term: "Avg PnL (USDC)",
              def: "Average paper profit/loss per resolved signal in USDC, calculated as the raw price-fraction PnL multiplied by your configured bet size. A strategy can have <50% win rate but still be profitable if wins are larger than losses.",
              note: "A positive avg PnL with ≥40% win rate is the target profile. Change the bet size on the Performance page to model different position sizes.",
            },
            {
              term: "Signal Count",
              def: "Total signals ever emitted by this strategy. More signals = more data to evaluate. Low counts (<20) make win rate statistics unreliable.",
              note: "Wait for 50+ resolved signals before drawing conclusions.",
            },
            {
              term: "Resolved Count",
              def: "Signals whose resolution window has expired and been evaluated. Unresolved signals are still within their window (2–6 hours after emission).",
              note: "New deployments will show low resolved counts until enough time has passed.",
            },
          ].map(({ term, def, note }) => (
            <div key={term} className="terminal-panel p-5">
              <div className="font-semibold text-sm mb-2 text-foreground font-mono">{term}</div>
              <p className="text-sm text-muted-foreground leading-relaxed mb-2">{def}</p>
              <div className="flex items-start gap-2 text-xs text-muted-foreground/70 bg-muted/20 rounded p-2">
                <AlertTriangle size={12} className="shrink-0 mt-0.5" />
                {note}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* FAQ */}
      <div>
        <SectionHeading><BookOpen size={18} className="text-primary" /> FAQ</SectionHeading>
        <div className="space-y-2">

          <FaqItem q="Is this real money? Could I actually lose anything?">
            No. The bot is in <strong>paper trading mode only</strong>. It detects opportunities and records them as signals, then tracks whether they would have been profitable — but it never places an order, holds a position, or touches any funds. The PnL figures are hypothetical.
          </FaqItem>

          <FaqItem q="How often does the data refresh?">
            The bot runs a full pipeline every <strong>5 minutes</strong>: it collects fresh snapshots for every watched market and runs all three strategy engines. The dashboard polls the API every <strong>30 seconds</strong> and updates in the browser automatically — you don't need to refresh.
          </FaqItem>

          <FaqItem q="What is a 'Neg Risk' market?">
            Polymarket groups related binary markets into events. When an event is structured so that exactly one outcome will resolve YES (e.g. "Who wins the Super Bowl?"), it's called a Neg Risk event. These are eligible for the over-round arbitrage strategy because the sum of YES prices must equal 1.0 at fair value.
          </FaqItem>

          <FaqItem q="Why do some signals show a score but no entry price?">
            The <code className="bg-muted px-1 rounded text-xs">neg_risk_overround</code> strategy fires on an entire event (multiple markets), not a single market, so there's no single entry price. The score represents the over-round in basis points across all legs.
          </FaqItem>

          <FaqItem q="What does 'score' actually mean — is higher always better?">
            Generally yes, but the scale differs per strategy. For <strong>spread_harvesting</strong> and <strong>mean_reversion</strong> it's 0–1 (fraction of spread that's profit / price delta magnitude). For <strong>neg_risk_overround</strong> it's the raw overround (0.05 = 5¢ edge across all legs). For <strong>micro_spread_scalp</strong> it's spread ÷ 0.10, capped at 1.0. For <strong>tail_yield_harvest</strong> it's the annualised yield percentage ÷ 5. For <strong>binary_arb</strong> it's guaranteed profit ÷ 0.05 (5¢ profit = score of 1.0). Don't compare scores across strategies — compare within a strategy over time.
          </FaqItem>

          <FaqItem q="Why is my win rate blank / showing '—'?">
            Win rate only shows once there are resolved signals for that strategy. A fresh deployment will show '—' until the first resolution window (2 hours for spread, 4 for reversion, 6 for neg-risk) has elapsed and at least one signal has been evaluated.
          </FaqItem>

          <FaqItem q="The market detail page shows no signal for a market — why?">
            The signal panel shows the most recent signal for that market within the last 7 days. If no signal was fired, the market either didn't meet any strategy's threshold during that period, or it was added to the watchlist recently and hasn't been analysed long enough.
          </FaqItem>

          <FaqItem q="Can this be connected to actually bet on Polymarket?">
            Yes — this is the planned Phase 4. Polymarket exposes a public CLOB (Central Limit Order Book) API. Adding an execution engine would require a funded USDC wallet on Polygon, an API key generated from that wallet, and order-signing via EIP-712. The neg-risk strategy is the cleanest candidate to automate first since it's purely mathematical and direction-agnostic. The data and signal track record you're building now is the foundation for that decision.
          </FaqItem>

          <FaqItem q="How are markets selected for the watchlist?">
            The market scanner queries Polymarket's Gamma API for all active markets, then filters and scores them on a combination of: 24h volume, liquidity depth, hours to close, and whether the market is part of a neg-risk event. The top-ranked markets by this composite score form the watchlist, refreshed hourly.
          </FaqItem>

          <FaqItem q="I see 'No signals match the current filter' — is the bot working?">
            Most likely yes. The signals table defaults to the last 24 hours. If the bot only started recently, or if no markets crossed the strategy thresholds in the last cycle, the table will be empty. Check the Overview page — if Data Snapshots is increasing over time, collection is working. You can also switch the filter to ALL and extend the window.
          </FaqItem>

          <FaqItem q="What is the bet size input on the Performance page?">
            The bet size (top-right of the Performance page, default $100) is a notional position size used to convert raw PnL fractions into real USDC dollar figures. For example, a raw PnL of +0.032 on a $100 bet = <strong>+$3.20</strong>. It affects the Avg PnL (USDC) columns in both tables and the Est. Total P&L summary card. The value is saved in your browser's local storage so it persists between sessions.
          </FaqItem>

        </div>
      </div>

      {/* Footer CTA */}
      <div className="terminal-panel p-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <div className="font-semibold text-sm mb-1">Ready to explore?</div>
          <p className="text-xs text-muted-foreground font-mono">Start with the Overview, then drill into a market that has an active signal.</p>
        </div>
        <div className="flex gap-3 shrink-0">
          <Link href="/">
            <button className="px-4 py-2 bg-primary text-primary-foreground text-xs font-medium rounded-md hover:bg-primary/90 transition-colors flex items-center gap-2">
              <LayoutDashboard size={14} /> Overview
            </button>
          </Link>
          <Link href="/signals">
            <button className="px-4 py-2 bg-card border border-border text-xs font-medium rounded-md hover:bg-accent transition-colors flex items-center gap-2">
              <Activity size={14} /> Signals
            </button>
          </Link>
        </div>
      </div>

    </motion.div>
  );
}
