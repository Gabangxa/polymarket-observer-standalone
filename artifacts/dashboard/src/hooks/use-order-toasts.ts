import { useEffect, useRef } from "react";
import { toast } from "@/hooks/use-toast";
import { useLiveOrders } from "@/hooks/use-polymarket";
import { useAlertSettings } from "@/hooks/use-alert-settings";

export function useOrderToasts() {
  const { settings } = useAlertSettings();
  const { data } = useLiveOrders({ status: "all", limit: 50 });

  // id → last-known status
  const prevStatuses = useRef<Map<number, string>>(new Map());
  // guards against firing toasts on the initial data load
  const initialized = useRef(false);

  useEffect(() => {
    if (!data?.orders) return;

    const prev = prevStatuses.current;
    const isFirstLoad = !initialized.current;
    initialized.current = true;

    const next = new Map<number, string>();

    for (const order of data.orders) {
      next.set(order.id, order.status);

      if (isFirstLoad) continue; // populate ref without toasting

      const prevStatus = prev.get(order.id);

      // New order we haven't seen yet
      if (prevStatus === undefined) {
        if (settings.onExecOpened && order.status === "SUBMITTED") {
          const label = (order as any).question?.slice(0, 60) ?? order.marketId;
          toast({ title: "Order opened", description: `${order.strategy} · ${label}` });
        }
        continue;
      }

      if (prevStatus === order.status) continue; // no change

      if (settings.onExecFailed && (order.status === "REJECTED" || order.status === "ERROR")) {
        const msg = (order as any).errorMsg ?? "order rejected by exchange";
        toast({
          title: "Execution failed",
          description: `${order.strategy}: ${msg.slice(0, 120)}`,
          variant: "destructive",
        });
      } else if (settings.onExecOpened && order.status === "SUBMITTED") {
        const label = (order as any).question?.slice(0, 60) ?? order.marketId;
        toast({ title: "Order opened", description: `${order.strategy} · ${label}` });
      } else if (settings.onExecClosed && order.status === "FILLED") {
        const label = (order as any).question?.slice(0, 60) ?? order.marketId;
        toast({ title: "Order filled", description: `${order.strategy} · ${label}` });
      }
    }

    prevStatuses.current = next;
  }, [data, settings]);
}
