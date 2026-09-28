// Klient API — typy odpowiadają odpowiedziom z tape/backend/tape/api.py.

export type Summary = {
  trades: number;
  wins: number;
  win_rate: number | null;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  avg_pnl: number | null;
  avg_r: number | null;
  r_trades: number;
  max_drawdown: number;
  t_stat: number | null;
};

export type Segment = {
  group: string;
  key: string;
  trades: number;
  net_pnl: number;
  avg_pnl: number;
  win_rate: number;
  t_vs_rest: number | null;
  significant: boolean;
};

export type Stats = {
  summary: Summary;
  equity: { t: string; equity: number }[];
  segments: Segment[];
};

export type Position = {
  symbol: string;
  direction: "long" | "short";
  opened_at: string;
  closed_at: string | null;
  qty: string;
  avg_entry: string;
  avg_exit: string | null;
  net_pnl: number;
  fees: number;
  r_multiple: number | null;
  initial_stop: string | null;
};

export type Impact = { direction: -1 | 0 | 1; magnitude: number; horizon: string };

export type MarketEvent = {
  id: string;
  title: string;
  category: "conflict" | "cb" | "macro" | "supply" | "market";
  lat: number;
  lon: number;
  place: string;
  summary: string;
  analog: string;
  sources: number;
  occurred_at: string;
  age_minutes: number;
  novelty: string;
  confidence: number;
  sample: boolean;
  impacts: Record<string, Impact>;
};

export type AssetBias = {
  score: number;
  label: "long" | "short" | "neutral";
  strength: string;
  events_used: number;
  drivers: { event_id: string; title: string; contribution: number }[];
};

export type BiasResponse = {
  XAU: AssetBias;
  XAG: AssetBias;
  track_record: { available: boolean; note: string };
  sample: boolean;
};

export type ImportReport = {
  detected: string | null;
  fills: number;
  new: number;
  duplicates: number;
  errors: string[];
  error_count: number;
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  stats: () => get<Stats>("/api/stats"),
  positions: () => get<Position[]>("/api/positions"),
  events: () => get<MarketEvent[]>("/api/events"),
  bias: () => get<BiasResponse>("/api/bias"),
  async importFile(file: File, broker: string, tz: string): Promise<ImportReport> {
    const body = new FormData();
    body.append("file", file);
    if (broker !== "auto") body.append("broker", broker);
    if (tz) body.append("tz", tz);
    const res = await fetch("/api/imports", { method: "POST", body });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json() as Promise<ImportReport>;
  },
};
