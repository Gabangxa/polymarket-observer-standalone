import { Component, type ReactNode } from "react";
import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { TimezoneProvider } from "@/hooks/use-timezone";
import { AlertSettingsProvider } from "@/hooks/use-alert-settings";
import NotFound from "@/pages/not-found";

class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (error) {
      return (
        <div style={{ padding: "2rem", fontFamily: "monospace", color: "#f87171" }}>
          <strong>Runtime error — page could not render</strong>
          <pre style={{ marginTop: "1rem", whiteSpace: "pre-wrap", fontSize: "0.8rem", color: "#94a3b8" }}>
            {(error as Error).message}
            {"\n\n"}
            {(error as Error).stack}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

// Components & Pages
import { Layout } from "@/components/layout";
import Overview from "@/pages/overview";
import Markets from "@/pages/markets";
import MarketDetail from "@/pages/market-detail";
import Signals from "@/pages/signals";
import Snapshots from "@/pages/snapshots";
import Performance from "@/pages/performance";
import Docs from "@/pages/docs";
import Execution from "@/pages/execution";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: true,
      staleTime: 10000,
    },
  },
});

function Router() {
  return (
    <Layout>
      <Switch>
        <Route path="/" component={Overview} />
        <Route path="/markets" component={Markets} />
        <Route path="/markets/:id" component={MarketDetail} />
        <Route path="/signals" component={Signals} />
        <Route path="/snapshots" component={Snapshots} />
        <Route path="/performance" component={Performance} />
        <Route path="/execution" component={Execution} />
        <Route path="/docs" component={Docs} />
        <Route component={NotFound} />
      </Switch>
    </Layout>
  );
}

function App() {
  return (
    <TimezoneProvider>
      <AlertSettingsProvider>
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <ErrorBoundary>
              <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
                <Router />
              </WouterRouter>
            </ErrorBoundary>
            <Toaster />
          </TooltipProvider>
        </QueryClientProvider>
      </AlertSettingsProvider>
    </TimezoneProvider>
  );
}

export default App;
