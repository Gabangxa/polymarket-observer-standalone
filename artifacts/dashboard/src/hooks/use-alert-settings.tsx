import { createContext, useContext, useState, useCallback } from "react";

export interface AlertSettings {
  onExecFailed: boolean;
  onExecOpened: boolean;
  onExecClosed: boolean;
}

const STORAGE_KEY = "observer:alert-settings";
const DEFAULTS: AlertSettings = {
  onExecFailed: true,
  onExecOpened: false,
  onExecClosed: false,
};

type AlertSettingsCtx = {
  settings: AlertSettings;
  set: <K extends keyof AlertSettings>(key: K, value: boolean) => void;
};

const Ctx = createContext<AlertSettingsCtx | null>(null);

export function AlertSettingsProvider({ children }: { children: React.ReactNode }) {
  const [settings, setSettings] = useState<AlertSettings>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : DEFAULTS;
    } catch {
      return DEFAULTS;
    }
  });

  const set = useCallback(<K extends keyof AlertSettings>(key: K, value: boolean) => {
    setSettings((prev) => {
      const next = { ...prev, [key]: value };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  return <Ctx.Provider value={{ settings, set }}>{children}</Ctx.Provider>;
}

export function useAlertSettings() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAlertSettings must be inside AlertSettingsProvider");
  return ctx;
}
