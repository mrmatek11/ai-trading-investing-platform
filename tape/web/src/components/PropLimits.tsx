import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api, type PropAccountStatus } from "../api";
import { money } from "../format";

const LEVEL = {
  ok: { label: "OK", text: "text-pos", bar: "bg-pos" },
  warn: { label: "uwaga", text: "text-warn", bar: "bg-warn" },
  danger: { label: "blisko limitu", text: "text-neg", bar: "bg-neg" },
  breached: { label: "limit złamany", text: "text-neg", bar: "bg-neg" },
} as const;

export function usePropAccounts() {
  return useQuery({ queryKey: ["prop-accounts"], queryFn: api.propAccounts, refetchInterval: 60_000 });
}

function Meter({ label, left, limit }: { label: string; left: number; limit: number }) {
  const share = limit > 0 ? Math.max(0, Math.min(1, left / limit)) : 1;
  const bar = share <= 0.25 ? "bg-neg" : share <= 0.5 ? "bg-warn" : "bg-pos";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className="num">
          {money(left, 0, false)} <span className="text-muted">/ {money(limit, 0, false)}</span>
        </span>
      </div>
      <div className="h-1.5 rounded bg-surface-2" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={limit} aria-valuenow={left}>
        <div className={`h-1.5 rounded ${bar}`} style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

function Card({ a }: { a: PropAccountStatus }) {
  const lv = LEVEL[a.level];
  return (
    <div className="flex flex-col gap-2.5 rounded-md border border-line px-4 py-3">
      <div className="flex items-baseline gap-2">
        <span className="font-medium">{a.name}</span>
        <span className={`text-xs ${lv.text}`}>{lv.label}</span>
        <div className="flex-1" />
        <span className="num text-xs text-muted">dziś {money(a.today_pnl)}</span>
      </div>
      <div className="num -mt-1.5 text-[11px] text-muted">
        {a.floating != null ? (
          <>
            otwarte pozycje <span className={a.floating < 0 ? "text-neg" : "text-pos"}>{money(a.floating)}</span> · equity z MT5
          </>
        ) : a.equity_ts ? (
          <span className="text-warn">brak świeżego equity z MT5 — tylko zamknięte transakcje</span>
        ) : (
          "tylko zamknięte transakcje"
        )}
      </div>
      <Meter label="Zostało na dziś" left={a.daily_left} limit={a.daily_limit} />
      <Meter label="Zostało do max drawdownu" left={a.overall_left} limit={a.overall_limit} />
    </div>
  );
}

// Karty limitów na dashboardzie — liczone z zamkniętych transakcji (bez niezrealizowanego wyniku).
export function PropLimits() {
  const q = usePropAccounts();
  if (!q.data?.length) return null;
  return (
    <section aria-label="Limity prop firm" className="flex flex-col gap-2">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {q.data.map((a) => (
          <Card key={a.id} a={a} />
        ))}
      </div>
      {q.data.some((a) => !a.equity_fresh) && (
        <p className="text-[11px] text-muted">
          Konta bez equity liczone z zamkniętych transakcji — otwarte pozycje z minusem zmniejszają zapas szybciej. EA Tape Sync 1.10 wysyła equity.
        </p>
      )}
    </section>
  );
}

// Pasek w nagłówku, gdy któreś konto jest blisko limitu albo go złamało.
export function PropAlertBar() {
  const q = usePropAccounts();
  const hot = (q.data ?? []).filter((a) => a.level === "danger" || a.level === "breached");
  if (!hot.length) return null;
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-neg/40 bg-neg/10 px-5 py-1.5 text-[13px]">
      {hot.map((a) => (
        <span key={a.id}>
          <span className="font-medium">{a.name}:</span>{" "}
          {a.level === "breached" ? "limit złamany" : `zostało ${money(a.daily_left, 0, false)} dziennego limitu`}
        </span>
      ))}
      <Link to="/risk" className="text-muted underline">szczegóły</Link>
    </div>
  );
}
