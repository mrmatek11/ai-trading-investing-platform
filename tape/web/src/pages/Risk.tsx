import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { api, type PropInput, type SizeInput } from "../api";
import { money, pct } from "../format";

const INSTRUMENTS = [
  { id: "XAUUSD", label: "XAUUSD · złoto (100 oz / lot)", size: 100 },
  { id: "XAGUSD", label: "XAGUSD · srebro (5 000 oz / lot)", size: 5000 },
  { id: "custom", label: "Inny (własna wielkość kontraktu)", size: 1 },
];

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-muted">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

const inputCls = "num h-9 rounded-md border border-line bg-surface px-2 text-fg";

function NumberInput({ value, onChange, step = "any" }: { value: string; onChange: (v: string) => void; step?: string }) {
  return <input type="number" inputMode="decimal" step={step} value={value} onChange={(e) => onChange(e.target.value)} className={inputCls} />;
}

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function Row({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-line-soft py-2 last:border-0">
      <span className="text-muted">{label}</span>
      <span className={`num ${tone}`}>{value}</span>
    </div>
  );
}

function Calculator() {
  const [f, setF] = useState({ balance: "10000", risk: "1", entry: "2684.20", stop: "2669.20", instrument: "XAUUSD", custom: "1", range: "", limit: "" });
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const size = f.instrument === "custom" ? Number(f.custom) : INSTRUMENTS.find((i) => i.id === f.instrument)!.size;
  const input: SizeInput = {
    balance: Number(f.balance),
    risk_pct: Number(f.risk),
    entry: Number(f.entry),
    stop: Number(f.stop),
    contract_size: size,
    daily_range: f.range ? Number(f.range) : null,
    daily_loss_limit: f.limit ? Number(f.limit) : null,
  };
  const debounced = useDebounced(input);
  const valid = [debounced.balance, debounced.risk_pct, debounced.entry, debounced.stop, debounced.contract_size].every((x) => Number.isFinite(x) && x > 0);
  const q = useQuery({ queryKey: ["size", debounced], queryFn: () => api.positionSize(debounced), enabled: valid, retry: false });
  const r = q.data;

  return (
    <section aria-label="Kalkulator pozycji" className="rounded-md border border-line p-5">
      <h2 className="text-[15px] font-medium">Kalkulator wielkości pozycji</h2>
      <p className="mt-1 text-muted">Ryzyko liczone od stop lossa; wielkość zaokrąglana w dół, więc nigdy nie ryzykujesz więcej niż zakładasz.</p>
      <div className="mt-4 grid gap-5 lg:grid-cols-2">
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <Field label="Instrument">
              <select value={f.instrument} onChange={(e) => set("instrument")(e.target.value)} className="h-9 rounded-md border border-line bg-surface px-2">
                {INSTRUMENTS.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {f.instrument === "custom" && (
            <div className="col-span-2">
              <Field label="Wielkość kontraktu (jednostek na 1 lot)">
                <NumberInput value={f.custom} onChange={set("custom")} />
              </Field>
            </div>
          )}
          <Field label="Saldo konta (USD)"><NumberInput value={f.balance} onChange={set("balance")} /></Field>
          <Field label="Ryzyko na transakcję (%)"><NumberInput value={f.risk} onChange={set("risk")} step="0.1" /></Field>
          <Field label="Cena wejścia"><NumberInput value={f.entry} onChange={set("entry")} /></Field>
          <Field label="Stop loss"><NumberInput value={f.stop} onChange={set("stop")} /></Field>
          <Field label="Średni dzienny zasięg (opcjonalnie)" hint="np. ATR(14) z D1, w cenie"><NumberInput value={f.range} onChange={set("range")} /></Field>
          <Field label="Dzienny limit straty (USD, opcjonalnie)" hint="z reguł prop firmy"><NumberInput value={f.limit} onChange={set("limit")} /></Field>
        </div>

        <div className="rounded-md border border-line bg-surface p-4">
          {!valid && <p className="text-muted">Uzupełnij saldo, ryzyko, wejście i stop loss.</p>}
          {q.isError && <p role="alert" className="text-neg">{(q.error as Error).message}</p>}
          {r && valid && (
            <>
              <div className="flex items-baseline gap-2">
                <span className={`num text-[32px] font-medium ${r.lots ? "" : "text-neg"}`}>{r.lots.toFixed(2).replace(".", ",")}</span>
                <span className="text-muted">lota</span>
              </div>
              <div className="mt-3">
                <Row label="Budżet ryzyka" value={money(r.risk_budget, 2, false)} />
                <Row label="Strata na stop lossie" value={money(-r.risk_actual)} tone={r.risk_actual ? "text-neg" : ""} />
                <Row label="Odległość SL" value={money(r.stop_distance, 2, false)} />
                <Row label="Wartość ruchu o 1,00" value={money(r.value_per_point, 2, false)} />
                <Row label="Wartość pozycji" value={money(r.notional, 0, false)} />
                {r.daily_range_loss != null && <Row label="Strata przy pełnym dziennym zasięgu" value={money(-r.daily_range_loss)} tone="text-neg" />}
                {r.daily_limit_share != null && <Row label="Część dziennego limitu na jeden SL" value={pct(r.daily_limit_share, 0)} tone={r.daily_limit_share > 0.5 ? "text-warn" : ""} />}
              </div>
              {r.warnings.length > 0 && (
                <ul className="mt-3 flex flex-col gap-1 text-warn">
                  {r.warnings.map((w) => <li key={w}>{w}</li>)}
                </ul>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}

const STATUS: Record<string, { label: string; tone: string }> = {
  active: { label: "aktywne", tone: "text-fg" },
  breached: { label: "złamane", tone: "text-neg" },
  passed: { label: "cel osiągnięty", tone: "text-pos" },
};

function PropTracker() {
  const [f, setF] = useState({ balance: "100000", daily: "5", dd: "10", type: "static" as PropInput["drawdown_type"], target: "10" });
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const m = useMutation({
    mutationFn: () =>
      api.prop({
        initial_balance: Number(f.balance),
        daily_loss_pct: Number(f.daily),
        max_drawdown_pct: Number(f.dd),
        drawdown_type: f.type,
        profit_target_pct: f.target ? Number(f.target) : null,
      }),
  });
  const rep = m.data?.report;
  const breachLabel = rep?.breach === "daily" ? "dzienny limit straty" : "maksymalny drawdown";

  return (
    <section aria-label="Reguły prop firmy" className="rounded-md border border-line p-5">
      <h2 className="text-[15px] font-medium">Reguły prop firmy na Twojej historii</h2>
      <p className="mt-1 text-muted">Większość oblanych challenge'y to złamana reguła, nie zła strategia. Sprawdź, czy Twoja historia przeżyłaby dane konto.</p>
      <form
        className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-6"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <Field label="Saldo startowe"><NumberInput value={f.balance} onChange={set("balance")} /></Field>
        <Field label="Dzienny limit (%)"><NumberInput value={f.daily} onChange={set("daily")} step="0.5" /></Field>
        <Field label="Max drawdown (%)"><NumberInput value={f.dd} onChange={set("dd")} step="0.5" /></Field>
        <Field label="Typ drawdownu">
          <select value={f.type} onChange={(e) => set("type")(e.target.value)} className="h-9 rounded-md border border-line bg-surface px-2">
            <option value="static">statyczny</option>
            <option value="trailing">trailing</option>
          </select>
        </Field>
        <Field label="Cel zysku (%)"><NumberInput value={f.target} onChange={set("target")} step="0.5" /></Field>
        <div className="flex items-end">
          <button type="submit" disabled={m.isPending} className="h-9 w-full rounded-md bg-fg font-medium text-bg disabled:opacity-40">
            {m.isPending ? "Liczę…" : "Sprawdź"}
          </button>
        </div>
      </form>

      {m.isError && <p role="alert" className="mt-3 text-neg">{m.error.message}</p>}
      {rep && (
        <div className="mt-5 grid gap-5 lg:grid-cols-2">
          <div>
            <p className="text-[15px]">
              Status: <span className={`font-medium ${STATUS[rep.status].tone}`}>{STATUS[rep.status].label}</span>
              {rep.status === "breached" && (
                <span className="text-muted"> — {breachLabel}, {rep.breach_day}</span>
              )}
              {rep.status === "passed" && <span className="text-muted"> — {rep.passed_day}</span>}
            </p>
            <div className="mt-3">
              <Row label="Saldo" value={money(rep.balance, 2, false)} />
              <Row label="Zapas do dziennego limitu (ostatni dzień)" value={money(rep.daily_headroom ?? 0, 2, false)} />
              <Row label="Zapas do max drawdownu" value={money(rep.overall_headroom ?? 0, 2, false)} tone={(rep.overall_headroom ?? 0) === 0 ? "text-neg" : ""} />
              <Row label="Dni z transakcjami" value={String(rep.days.length)} />
            </div>
            <p className="mt-3 text-xs text-muted">{m.data!.note}</p>
          </div>
          <div>
            <h3 className="mb-2 text-muted">Ta sama historia na innych kontach</h3>
            <table className="w-full text-[12px]">
              <tbody>
                {Object.entries(m.data!.simulation).map(([k, s]) => (
                  <tr key={k} className="border-b border-line-soft last:border-0">
                    <td className="py-2">{s.name}</td>
                    <td className={`py-2 text-right ${STATUS[s.status]?.tone ?? ""}`}>
                      {STATUS[s.status]?.label ?? s.status}
                      {s.breach_day ? ` · ${s.breach_day}` : s.passed_day ? ` · ${s.passed_day}` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

export function RiskPage() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5 px-6 py-6">
      <h1 className="text-xl font-semibold tracking-tight">Ryzyko</h1>
      <Calculator />
      <PropTracker />
    </div>
  );
}
