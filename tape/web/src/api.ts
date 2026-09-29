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

export type GroupStats = { key: string; trades: number; net_pnl: number; win_rate: number; avg_pnl: number; avg_r: number | null };

export type Stats = {
  setups: GroupStats[];
  mistakes: GroupStats[];
  summary: Summary;
  equity: { t: string; equity: number }[];
  segments: Segment[];
};

export type Position = {
  key: string;
  setup: string | null;
  mistakes: string[];
  has_notes: boolean;
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

export type JournalData = {
  setup_id: number | null;
  checklist: Record<string, boolean>;
  mistakes: string[];
  notes: string;
  initial_stop: string | null;
};

export type EconEvent = { ts: string; country: string; title: string; impact: "high" | "medium" | "low"; forecast: string; previous: string; source: string };

export type PositionDetail = {
  position: Position;
  fills: { id: string; ts: string; side: "buy" | "sell"; qty: string; price: string; fee: number; broker_pnl: number | null }[];
  journal: JournalData | null;
  prices: { t: string; p: number }[];
  events: EconEvent[];
};

export type Setup = { id: number; name: string; description: string; rules: string[]; stats: GroupStats | null };
export type SetupInput = { name: string; description: string; rules: string[] };

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
  track_record: {
    available: boolean;
    note: string;
    observations?: number;
    hit_rate?: number | null;
    t_stat?: number | null;
    labels_allowed?: boolean;
  };
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

export type Mapping = {
  columns: Record<string, string>;
  tz: string;
  date_format: string | null;
  buy_values: string[] | null;
  sell_values: string[] | null;
};

export type SuggestResponse = {
  headers: string[];
  preview: Record<string, string | null>[];
  rows: number;
  ai_available: boolean;
  suggestion: Mapping & { source: "heuristic" | "ai"; confidence: Record<string, number>; missing: string[]; notes: string };
};

export type SizeInput = {
  balance: number;
  risk_pct: number;
  entry: number;
  stop: number;
  contract_size: number;
  daily_range?: number | null;
  daily_loss_limit?: number | null;
};

export type SizeResult = {
  lots: number;
  risk_budget: number;
  risk_actual: number;
  stop_distance: number;
  value_per_point: number;
  notional: number;
  min_lot_risk: number;
  daily_range_loss: number | null;
  daily_limit_share: number | null;
  warnings: string[];
};

export type PropInput = {
  initial_balance: number;
  daily_loss_pct: number;
  max_drawdown_pct: number;
  drawdown_type: "static" | "trailing";
  profit_target_pct: number | null;
};

export type PropDay = { day: string; end_balance: number; pnl: number; daily_floor: number; overall_floor: number; breach: string | null };

export type PropResponse = {
  report: {
    status: "active" | "breached" | "passed";
    balance: number;
    breach: "daily" | "max_drawdown" | null;
    breach_day: string | null;
    passed_day: string | null;
    daily_headroom: number | null;
    overall_headroom: number | null;
    days: PropDay[];
  };
  simulation: Record<string, { name: string; status: string; breach: string | null; breach_day: string | null; passed_day: string | null }>;
  note: string;
};

export type Quote = { asset: "XAU" | "XAG"; price: number; ts: string; age_minutes: number; change_24h: number | null; provider: string };

export type ReviewFinding = { title: string; detail: string; facts: string[] };

export type AiReview = {
  headline: string;
  strengths: ReviewFinding[];
  leaks: ReviewFinding[];
  actions: { text: string; facts: string[] }[];
  caveat: string;
  dropped: number;
  created_at: string;
  stale: boolean;
  facts: { id: string; text: string }[];
};

export type ReviewState = { ai_available: boolean; trades: number; min_trades: number; review: AiReview | null };

export type CashFlowItem = { id: number; ts: string; amount: number; currency: string; note: string; source: string };

export type Portfolio = {
  note: string;
  balance: number | null;
  realized: number;
  deposits: number;
  twr: number | null;
  ytd: number | null;
  currencies: string[];
  exposure: Record<"XAU" | "XAG", { ounces: number; price: number | null; notional: number | null; price_ts: string | null }>;
  holdings: {
    key: string;
    symbol: string;
    metal: "XAU" | "XAG" | null;
    direction: "long" | "short";
    qty: string;
    avg_price: string;
    ounces: number | null;
    mark: number | null;
    unrealized: number | null;
  }[];
  months: { month: string; pnl: number; flows: number; start_equity: number; end_equity: number; ret: number | null }[];
  cash_flows: CashFlowItem[];
};

export type Connection = {
  id: string;
  kind: "ibkr_flex" | "mt5_push";
  label: string;
  tz: string;
  created_at: string;
  last_sync_at: string | null;
  last_status: "never" | "ok" | "partial" | "error";
  last_new: number;
  last_error: string;
};

export type SyncReport = { new: number; duplicates: number; errors: string[]; connection: Connection };

// Token logowania (Clerk) — ustawiany przez AuthGate; w trybie jednego użytkownika brak.
let tokenProvider: (() => Promise<string | null>) | null = null;

export function setTokenProvider(fn: (() => Promise<string | null>) | null) {
  tokenProvider = fn;
}

async function authed(path: string, init: RequestInit = {}): Promise<Response> {
  const token = tokenProvider ? await tokenProvider() : null;
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) throw new Error("Sesja wygasła — zaloguj się ponownie.");
  return res;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  return send<T>("POST", path, body);
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await authed(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    const d = detail?.detail;
    // 422 z FastAPI: lista błędów walidacji pól
    const msg = typeof d === "string" ? d : Array.isArray(d) ? `Niepoprawne pole: ${d.map((e: { loc?: string[] }) => e.loc?.at(-1)).join(", ")}` : `Błąd ${res.status}`;
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await authed(path);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export type PropAccountStatus = {
  id: number;
  book: string;
  name: string;
  day: string;
  status: "active" | "breached" | "passed";
  level: "ok" | "warn" | "danger" | "breached";
  balance: number;
  today_pnl: number;
  daily_limit: number;
  daily_left: number;
  overall_limit: number;
  overall_left: number;
  rules: { initial_balance: number; daily_loss_pct: number; max_drawdown_pct: number; drawdown_type: "static" | "trailing"; profit_target_pct: number | null; day_tz: string };
};

export type Book = { id: string; label: string; kind: "mt5_push" | "ibkr_flex" | "import"; has_trades: boolean };

// null = wszystkie rachunki; "" to też rachunek (import z plików bez nazwy)
const bq = (book: string | null) => (book === null ? "" : `?book=${encodeURIComponent(book)}`);

export const api = {
  stats: (book: string | null = null) => get<Stats>(`/api/stats${bq(book)}`),
  positions: (book: string | null = null) => get<Position[]>(`/api/positions${bq(book)}`),
  events: () => get<MarketEvent[]>("/api/events"),
  bias: () => get<BiasResponse>("/api/bias"),
  positionSize: (input: SizeInput) => post<SizeResult>("/api/tools/position-size", input),
  prop: (input: PropInput, book: string | null = null) => post<PropResponse>(`/api/prop/evaluate${bq(book)}`, input),
  position: (key: string) => get<PositionDetail>(`/api/positions/${encodeURIComponent(key)}`),
  saveJournal: (key: string, body: Omit<JournalData, "initial_stop"> & { initial_stop: number | null }) =>
    send<{ ok: boolean }>("PUT", `/api/positions/${encodeURIComponent(key)}/journal`, body),
  journalMeta: () => get<{ mistakes: string[] }>("/api/journal/meta"),
  setups: (book: string | null = null) => get<Setup[]>(`/api/setups${bq(book)}`),
  books: () => get<Book[]>("/api/books"),
  propAccounts: () => get<PropAccountStatus[]>("/api/prop/accounts"),
  savePropAccount: (body: PropInput & { book: string; name: string; day_tz: string }) => send<{ id: number }>("PUT", "/api/prop/accounts", body),
  deletePropAccount: (id: number) => send<{ ok: boolean }>("DELETE", `/api/prop/accounts/${id}`),
  createSetup: (body: SetupInput) => send<{ id: number }>("POST", "/api/setups", body),
  updateSetup: (id: number, body: SetupInput) => send<{ ok: boolean }>("PUT", `/api/setups/${id}`, body),
  deleteSetup: (id: number) => send<{ ok: boolean }>("DELETE", `/api/setups/${id}`),
  calendar: (days = 7) => get<{ events: EconEvent[]; sources: string[]; note: string }>(`/api/calendar?days=${days}`),
  quotes: () => get<Quote[]>("/api/market/quotes"),
  review: () => get<ReviewState>("/api/review"),
  generateReview: () => send<AiReview>("POST", "/api/review"),
  portfolio: (book: string | null = null) => get<Portfolio>(`/api/portfolio${bq(book)}`),
  addCashFlow: (body: { book: string; ts: string; amount: number; currency: string; note: string }) =>
    send<CashFlowItem>("POST", "/api/cashflows", body),
  deleteCashFlow: (id: number) => send<{ ok: boolean }>("DELETE", `/api/cashflows/${id}`),
  connections: () => get<{ connections: Connection[]; encryption: boolean }>("/api/connections"),
  addIbkr: (body: { label: string; token: string; query_id: string; tz: string }) =>
    send<Connection>("POST", "/api/connections/ibkr", body),
  addMt5: (label: string) => send<Connection & { token: string }>("POST", "/api/connections/mt5", { label }),
  syncConnection: (id: string) => send<SyncReport>("POST", `/api/connections/${encodeURIComponent(id)}/sync`),
  deleteConnection: (id: string) => send<{ ok: boolean }>("DELETE", `/api/connections/${encodeURIComponent(id)}`),
  async suggestMapping(file: File): Promise<SuggestResponse> {
    const body = new FormData();
    body.append("file", file);
    const res = await authed("/api/imports/suggest", { method: "POST", body });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json() as Promise<SuggestResponse>;
  },
  async importFile(file: File, broker: string, tz: string, mapping?: Mapping, book = ""): Promise<ImportReport> {
    const body = new FormData();
    body.append("file", file);
    if (book) body.append("book", book);
    if (mapping) body.append("mapping", JSON.stringify(mapping));
    else if (broker !== "auto") body.append("broker", broker);
    if (tz && !mapping) body.append("tz", tz);
    const res = await authed("/api/imports", { method: "POST", body });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return res.json() as Promise<ImportReport>;
  },
};
