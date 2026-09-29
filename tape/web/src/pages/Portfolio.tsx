import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { api, type Portfolio } from "../api";
import { money, num, pct, price, tone, when } from "../format";

const MONTHS = ["Sty", "Lut", "Mar", "Kwi", "Maj", "Cze", "Lip", "Sie", "Wrz", "Paź", "Lis", "Gru"];
const METAL = { XAU: "Złoto", XAG: "Srebro" } as const;

function Kpi({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="flex flex-col gap-1.5 border-r border-line px-4 py-3.5 last:border-r-0">
      <span className="text-xs text-muted">{label}</span>
      <span className={`num text-[22px] font-medium ${className}`}>{value}</span>
    </div>
  );
}

function signedPct(v: number | null) {
  if (v == null) return "—";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${pct(Math.abs(v))}`;
}

function Exposure({ data }: { data: Portfolio }) {
  return (
    <section aria-label="Ekspozycja na metale" className="grid gap-3 sm:grid-cols-2">
      {(["XAU", "XAG"] as const).map((m) => {
        const e = data.exposure[m];
        const side = e.ounces > 0 ? "long" : e.ounces < 0 ? "short" : "brak";
        return (
          <div key={m} className="rounded-md border border-line px-4 py-3.5">
            <div className="flex items-baseline justify-between">
              <h2 className="text-[13px] font-medium">
                {METAL[m]} <span className="num text-muted">{m}</span>
              </h2>
              <span className={`text-xs ${side === "long" ? "text-pos" : side === "short" ? "text-neg" : "text-muted"}`}>{side}</span>
            </div>
            <div className="num mt-1.5 text-[22px] font-medium">{num(e.ounces, e.ounces % 1 ? 2 : 0, true)} oz</div>
            <div className="num text-xs text-muted">
              {e.notional != null ? `${money(e.notional, 0)} USD` : "brak ceny do wyceny"}
              {e.price != null && e.price_ts && ` · ${price(String(e.price))} z ${when(e.price_ts)}`}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function MonthlyGrid({ months }: { months: Portfolio["months"] }) {
  const years = [...new Set(months.map((m) => m.month.slice(0, 4)))].sort().reverse();
  const byKey = new Map(months.map((m) => [m.month, m]));
  const hasReturns = months.some((m) => m.ret != null);
  return (
    <section aria-label="Wyniki miesięczne" className="overflow-x-auto rounded-md border border-line">
      <div className="flex items-baseline gap-3 px-4 pt-3">
        <h2 className="text-[13px] font-medium">{hasReturns ? "Stopa zwrotu miesięcznie" : "Wynik miesięcznie"}</h2>
        <span className="text-xs text-muted">{hasReturns ? "Modified Dietz, na saldzie; rok = łańcuchowo (TWR)" : "USD — dodaj wpłaty, żeby zobaczyć %"}</span>
      </div>
      <table className="num mt-2 w-full min-w-[760px] text-right text-[13px]">
        <thead className="text-xs text-muted">
          <tr>
            <th className="px-3 py-1.5 text-left font-normal">Rok</th>
            {MONTHS.map((m) => (
              <th key={m} className="px-2 py-1.5 font-normal">
                {m}
              </th>
            ))}
            <th className="px-3 py-1.5 font-normal">Rok</th>
          </tr>
        </thead>
        <tbody>
          {years.map((y) => {
            const cells = MONTHS.map((_, i) => byKey.get(`${y}-${String(i + 1).padStart(2, "0")}`));
            const rets = cells.map((c) => c?.ret).filter((x): x is number => x != null);
            const yearRet = rets.length ? rets.reduce((a, b) => a * (1 + b), 1) - 1 : null;
            const yearPnl = cells.reduce((a, c) => a + (c?.pnl ?? 0), 0);
            return (
              <tr key={y} className="border-t border-line-soft">
                <td className="px-3 py-2 text-left text-muted">{y}</td>
                {cells.map((c, i) => (
                  <td key={i} className={`px-2 py-2 ${c ? tone(hasReturns ? c.ret : c.pnl) : "text-muted"}`} title={c ? `${money(c.pnl)} USD` : undefined}>
                    {!c ? "" : hasReturns ? signedPct(c.ret) : money(c.pnl, 0)}
                  </td>
                ))}
                <td className={`px-3 py-2 font-medium ${tone(hasReturns ? yearRet : yearPnl)}`}>{hasReturns ? signedPct(yearRet) : money(yearPnl, 0)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function CashFlows({ data }: { data: Portfolio }) {
  const qc = useQueryClient();
  const [ts, setTs] = useState(() => new Date().toISOString().slice(0, 10));
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["portfolio"] });
  const add = useMutation<unknown, Error>({
    mutationFn: () =>
      api.addCashFlow({
        ts: `${ts}T00:00:00Z`,
        amount: Number(amount.replace(",", ".").replace(/\s/g, "")),
        currency: "USD",
        note,
      }),
    onSuccess: () => {
      setAmount("");
      setNote("");
      refresh();
    },
  });
  const remove = useMutation<unknown, Error, number>({
    mutationFn: (id) => api.deleteCashFlow(id),
    onSuccess: refresh,
  });
  const flows = [...data.cash_flows].reverse();
  return (
    <section aria-label="Wpłaty i wypłaty" className="rounded-md border border-line">
      <div className="flex items-baseline gap-3 px-4 pt-3">
        <h2 className="text-[13px] font-medium">Wpłaty i wypłaty</h2>
        <span className="text-xs text-muted">z MT5 i IBKR pobierane automatycznie; ręcznie np. saldo początkowe</span>
      </div>
      <form
        className="flex flex-wrap items-end gap-2 px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <label className="flex flex-col gap-1 text-xs text-muted">
          Data
          <input
            type="date"
            value={ts}
            onChange={(e) => setTs(e.target.value)}
            required
            className="num h-8 rounded-md border border-line bg-surface px-2 text-[13px] text-fg"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          Kwota USD (− wypłata)
          <input
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
            inputMode="decimal"
            placeholder="10000"
            className="num h-8 w-32 rounded-md border border-line bg-surface px-2 text-[13px] text-fg"
          />
        </label>
        <label className="flex min-w-40 flex-1 flex-col gap-1 text-xs text-muted">
          Opis
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={200}
            placeholder="saldo początkowe"
            className="h-8 rounded-md border border-line bg-surface px-2 text-[13px] text-fg"
          />
        </label>
        <button type="submit" disabled={add.isPending || !amount.trim()} className="h-8 rounded-md bg-fg px-3 font-medium text-bg disabled:opacity-40">
          Dodaj
        </button>
        {add.isError && (
          <span role="alert" className="w-full text-neg">
            {add.error.message}
          </span>
        )}
      </form>
      {flows.length > 0 && (
        <ul className="border-t border-line">
          {flows.map((f) => (
            <li key={f.id} className="flex items-center gap-3 border-b border-line-soft px-4 py-2 last:border-b-0">
              <span className="num w-24 text-muted">{f.ts.slice(0, 10)}</span>
              <span className={`num w-32 text-right ${tone(f.amount)}`}>
                {money(f.amount)} {f.currency}
              </span>
              <span className="min-w-0 flex-1 truncate text-muted">{f.note}</span>
              <span className="text-xs uppercase text-muted">{f.source}</span>
              {f.source === "manual" ? (
                <button type="button" onClick={() => remove.mutate(f.id)} className="text-xs text-muted hover:text-neg" aria-label="Usuń operację">
                  Usuń
                </button>
              ) : (
                <span className="w-[30px]" />
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function PortfolioPage() {
  const q = useQuery({ queryKey: ["portfolio"], queryFn: api.portfolio });
  if (q.isLoading) return <div className="p-6 text-muted">Ładowanie…</div>;
  if (q.isError) return <div className="p-6 text-neg">Nie udało się pobrać portfela: {q.error.message}</div>;
  const d = q.data!;
  const multiCurrency = d.currencies.length > 1;

  return (
    <div className="flex min-w-0 flex-col gap-4 px-4 py-5 sm:px-6">
      <h1 className="text-xl font-semibold tracking-tight">Portfel</h1>

      <section aria-label="Kluczowe liczby" className="grid grid-cols-2 rounded-md border border-line md:grid-cols-5">
        <Kpi label="Saldo (bez otwartych)" value={d.balance != null ? money(d.balance, 2, false) : "—"} />
        <Kpi label="Wpłaty netto" value={money(d.deposits, 2, false)} />
        <Kpi label="Wynik zrealizowany" value={money(d.realized)} className={tone(d.realized)} />
        <Kpi label="Stopa zwrotu (TWR)" value={signedPct(d.twr)} className={tone(d.twr)} />
        <Kpi label={`Od początku ${new Date().getFullYear()}`} value={signedPct(d.ytd)} className={tone(d.ytd)} />
      </section>
      {(d.note || multiCurrency) && (
        <p className="text-xs text-warn">
          {d.note}
          {multiCurrency && ` Operacje w kilku walutach (${d.currencies.join(", ")}) — sumujemy bez przeliczania kursów.`}
        </p>
      )}

      <Exposure data={d} />

      <section aria-label="Otwarte pozycje" className="min-w-0 rounded-md border border-line">
        <h2 className="px-4 pt-3 text-[13px] font-medium">Otwarte pozycje</h2>
        {d.holdings.length === 0 ? (
          <p className="px-4 py-3 text-muted">Brak otwartych pozycji w zaimportowanej historii.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="num mt-2 w-full min-w-[520px] text-right text-[13px]">
              <thead className="text-xs text-muted">
                <tr>
                  <th className="px-4 py-1.5 text-left font-normal">Instrument</th>
                  <th className="px-3 py-1.5 font-normal">Wielkość</th>
                  <th className="px-3 py-1.5 font-normal">Śr. cena</th>
                  <th className="px-3 py-1.5 font-normal">Uncje</th>
                  <th className="px-4 py-1.5 font-normal">Niezrealizowany</th>
                </tr>
              </thead>
              <tbody>
                {d.holdings.map((h) => (
                  <tr key={h.key} className="border-t border-line-soft">
                    <td className="px-4 py-2 text-left">
                      <Link to="/trades/$key" params={{ key: h.key }} className="hover:underline">
                        {h.symbol}
                      </Link>{" "}
                      <span className={h.direction === "long" ? "text-pos" : "text-neg"}>{h.direction}</span>
                    </td>
                    <td className="px-3 py-2">{h.qty}</td>
                    <td className="px-3 py-2">{price(h.avg_price)}</td>
                    <td className="px-3 py-2">{h.ounces != null ? num(h.ounces, 0, true) : "—"}</td>
                    <td className={`px-4 py-2 ${tone(h.unrealized)}`} title={h.unrealized == null && h.metal ? "Futures: bez wyceny spotem (baza)" : undefined}>
                      {h.unrealized != null ? money(h.unrealized) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {d.months.length > 0 && <MonthlyGrid months={d.months} />}
      <CashFlows data={d} />
    </div>
  );
}
