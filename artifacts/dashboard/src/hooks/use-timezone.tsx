import { createContext, useContext, useState, useEffect } from "react";

export const TIMEZONES = [
  { value: "UTC",                    label: "UTC" },
  { value: "America/New_York",       label: "New York (ET)" },
  { value: "America/Chicago",        label: "Chicago (CT)" },
  { value: "America/Denver",         label: "Denver (MT)" },
  { value: "America/Los_Angeles",    label: "Los Angeles (PT)" },
  { value: "Europe/London",          label: "London (GMT/BST)" },
  { value: "Europe/Paris",           label: "Paris (CET)" },
  { value: "Africa/Johannesburg",    label: "Johannesburg (SAST)" },
  { value: "Asia/Dubai",             label: "Dubai (GST)" },
  { value: "Asia/Singapore",         label: "Singapore (SGT)" },
  { value: "Asia/Tokyo",             label: "Tokyo (JST)" },
  { value: "Australia/Sydney",       label: "Sydney (AEST)" },
];

const STORAGE_KEY = "polymarket_tz";

function detectLocalTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "UTC";
  }
}

function resolveDefault(): string {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && TIMEZONES.some((t) => t.value === stored)) return stored;
  const local = detectLocalTz();
  if (TIMEZONES.some((t) => t.value === local)) return local;
  return "UTC";
}

type TimezoneContextValue = {
  timezone: string;
  setTimezone: (tz: string) => void;
};

const TimezoneContext = createContext<TimezoneContextValue>({
  timezone: "UTC",
  setTimezone: () => {},
});

export function TimezoneProvider({ children }: { children: React.ReactNode }) {
  const [timezone, setTimezoneState] = useState<string>(resolveDefault);

  const setTimezone = (tz: string) => {
    setTimezoneState(tz);
    localStorage.setItem(STORAGE_KEY, tz);
  };

  return (
    <TimezoneContext.Provider value={{ timezone, setTimezone }}>
      {children}
    </TimezoneContext.Provider>
  );
}

export function useTimezone() {
  return useContext(TimezoneContext);
}
