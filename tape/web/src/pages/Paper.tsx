import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type PaperDay, type PaperOverview, type PaperProgress, type PaperRun } from "../api";
import { useBook } from "../book";
import { money, num, tone } from "../format";

const STATUS: Record<PaperDay["status"], string> = {
  trade: "transakcja",
  no_nr7: "dzień nieaktywny (brak NR7)",
  no_setup: "brak zakresu 08:00–09:00",
  no_fill: "zlecenia nie wypełnione do 12:00",
  data_gap: "pominięty — dziura w danych",
};
const RUN_STATUS: Record<PaperRun["status"], string> = { active: "aktywny", stopped: "zatrzymany", mismatch: "kod zmieniony" };
const hhmm = (iso: string | null) => (iso ? new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" }) : "—");

// Postęp względem karty zamrożenia: liczba transakcji i t-stat na wyniku za uncję po koszcie z badania.
function Meter({ label, value, need, text }: { label: string; value: number; need: number; text: string }) {
  const share = Math.max(0, Math.min(1, need > 0 ? value / need : 0));
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline gap-2 text-xs">
        <span className="text-muted">{label}</span>
        <div className="flex-1" />
        <span className="num">{text}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" role="meter" aria-label={label} aria-valuenow={value} aria-valuemax={need}>
        <div className={`h-full rounded-full ${share >= 1 ? "bg-pos" : "bg-accent"}`} style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

function Progress({ p }: { p: PaperProgress }) {
  return (
    <div className="flex flex-col gap-3">
      <Meter label="Transakcje" value={p.trades} need={p.need_trades} text={`${p.trades} / ${p.need_trades}`} />
      <Meter label="t-stat (wynik po kosztach 0,40 USD/oz)" value={Math.max(0, p.t_stat ?? 0)} need={p.need_t}
        text={`${p.t_stat == null ? "—" : num(p.t_stat, 2)} / ${num(p.need_t, 2)}`} />
      <p className="text-xs text-muted">
        Średnio <span className={`num ${tone(p.mean_bps)}`}>{p.mean_bps == null ? "—" : `${num(p.mean_bps, 1, true)} bps`}</span>
        {p.expected_bps != null && <> · badanie poza próbą: <span className="num">+{num(p.expected_bps, 1)} bps</span></>}
      </p>
      <p className={`text-[13px] ${p.passed ? "text-pos" : ""}`}>{p.verdict}</p>
    </div>
  );
}

function NewRun({ d }: { d: PaperOverview }) {
  const qc = useQueryClient();
  const v = d.versions[0];
  const [balance, setBalance] = useState("10000");
  const [risk, setRisk] = useState("0.5");
  const [name, setName] = useState("");
  const create = useMutation<PaperRun, Error>({
    mutationFn: () => api.createPaperRun({ version: v.id, name, balance: Number(balance), risk_pct: Number(risk) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["paper"] });
      qc.invalidateQueries({ queryKey: ["books"] });
    },
  });
  if (!v) return null;
  const riskUsd = (Number(balance) * Number(risk)) / 100;
  return (
    <form
      className="flex flex-col gap-4 rounded-md border border-line p-5"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-[13px] font-medium">Nowy przebieg paper</h2>
        <span className="num text-xs text-muted">{v.id}</span>
        {!v.frozen_ok && <span className="text-xs text-neg">kod silnika różni się od zamrożonego — start zablokowany</span>}
      </div>
      <p className="text-xs leading-relaxed text-muted">
        {v.name}: {v.description} Wersja jest zamrożona — przebieg handluje tylko dniami po starcie, na tych samych regułach co
        badanie. Każda zmiana kodu to nowa wersja i test od zera.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1.5">
          <span className="text-muted">Nazwa (opcjonalnie)</span>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder={v.name}
            className="h-9 rounded-md border border-line bg-surface px-2 placeholder:text-muted" />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-muted">Saldo startowe (USD)</span>
          <input type="number" min={100} step={100} value={balance} onChange={(e) => setBalance(e.target.value)}
            className="num h-9 rounded-md border border-line bg-surface px-2" />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-muted">Ryzyko na transakcję (%) — max {d.max_risk_pct}</span>
          <input type="number" min={0.1} max={d.max_risk_pct} step={0.1} value={risk} onChange={(e) => setRisk(e.target.value)}
            className="num h-9 rounded-md border border-line bg-surface px-2" />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-muted">
          {Number.isFinite(riskUsd) && riskUsd > 0 ? `Strata na stopie ≈ ${money(riskUsd, 0, false)} USD na transakcję.` : ""} Wielkość liczona
          w dół do 0,01 lota.
        </span>
        <div className="flex-1" />
        {create.isError && <span role="alert" className="text-xs text-neg">{create.error.message}</span>}
        <button type="submit" disabled={create.isPending || !v.frozen_ok}
          className="h-9 rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40">
          Uruchom
        </button>
      </div>
    </form>
  );
}

function RunDetail({ id }: { id: string }) {
  const qc = useQueryClient();
  const { setBook } = useBook();
  const q = useQuery({ queryKey: ["paper", id], queryFn: () => api.paperRun(id), refetchInterval: 60_000 });
  const stop = useMutation({ mutationFn: () => api.stopPaperRun(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["paper"] }) });
  const r = q.data;
  if (!r) return <p className="text-muted">Ładuję…</p>;
  const trades = r.days.filter((d) => d.status === "trade");
  return (
    <section aria-label={`Przebieg ${r.name}`} className="flex flex-col gap-4 rounded-md border border-line p-5">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-[15px] font-semibold">{r.name}</h2>
        <span className={`text-xs ${r.status === "active" ? "text-pos" : r.status === "mismatch" ? "text-neg" : "text-muted"}`}>
          {RUN_STATUS[r.status]}
        </span>
        <span className="num text-xs text-muted">od {r.first_day} · ryzyko {num(r.risk_pct, 1)}%</span>
        <div className="flex-1" />
        <span className="num">
          {money(r.equity, 2, false)} USD <span className={tone(r.equity - r.balance_start)}>({money(r.equity - r.balance_start, 2)})</span>
        </span>
      </div>
      {!r.counts_for_card && (
        <p className="text-xs text-warn">
          Świece od dostawcy {r.providers.join(", ")} — karta zamrożenia wymaga świec po stronie bid (OANDA). Ten przebieg jest
          orientacyjny i nie liczy się jako test.
        </p>
      )}
      <Progress p={r.progress} />
      {r.today && (
        <div className="rounded-md border border-line-soft px-4 py-3 text-[13px]">
          <span className="font-medium">Dziś ({r.today.day}): </span>
          {!r.today.active ? (
            <span className="text-muted">wczoraj nie był dzień NR7 — bez transakcji.</span>
          ) : r.today.orders.length === 0 ? (
            <span className="text-muted">dzień aktywny — poziomy po zamknięciu zakresu 08:00–09:00 Londynu.</span>
          ) : (
            <span className="num">
              {r.today.orders.map((o) => `${o.direction === 1 ? "kupno" : "sprzedaż"} stop ${num(o.entry, 2)} (SL ${num(o.stop, 2)})`).join(" · ")}
              <span className="text-muted"> · ważne do {hhmm(r.today.orders[0].valid_until)}, zamknięcie do {hhmm(r.today.orders[0].flat_by)}</span>
            </span>
          )}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-[13px]">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th className="py-1.5 font-normal">Dzień</th>
              <th className="font-normal">Wynik dnia</th>
              <th className="text-right font-normal">Wejście</th>
              <th className="text-right font-normal">Wyjście</th>
              <th className="text-right font-normal">USD/oz po kosztach</th>
              <th className="text-right font-normal">Loty</th>
              <th className="text-right font-normal">P/L USD</th>
            </tr>
          </thead>
          <tbody>
            {r.days.map((d) => (
              <tr key={d.day} className="border-t border-line-soft">
                <td className="num py-1.5">{d.day}</td>
                <td className={d.status === "trade" ? "" : "text-muted"}>
                  {d.status === "trade" ? `${d.direction === 1 ? "long" : "short"} · ${d.reason === "sl" ? "stop loss" : "zamknięcie 16:00"}` : STATUS[d.status]}
                  {d.note && <span className="block text-xs text-muted">{d.note}</span>}
                </td>
                <td className="num text-right">{d.entry == null ? "" : num(d.entry, 2)}</td>
                <td className="num text-right">{d.exit == null ? "" : num(d.exit, 2)}</td>
                <td className={`num text-right ${tone(d.net)}`}>{d.net == null ? "" : num(d.net, 2, true)}</td>
                <td className="num text-right">{d.lots == null ? "" : num(d.lots, 2)}</td>
                <td className={`num text-right ${tone(d.pnl_usd)}`}>{d.pnl_usd == null ? "" : money(d.pnl_usd)}</td>
              </tr>
            ))}
            {r.days.length === 0 && (
              <tr>
                <td colSpan={7} className="py-3 text-muted">
                  Pierwszy dzień przebiegu to {r.first_day}. Dni rozliczają się po 16:00 Londynu.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
        <span>
          {trades.length} transakcji · „USD/oz po kosztach” = wynik minus stały koszt 0,40 USD/oz z badania (na tym liczymy kryterium)
        </span>
        <div className="flex-1" />
        <button type="button" onClick={() => setBook(r.book)} className="h-8 rounded-md border border-line px-3 text-fg">
          Pokaż w journalu
        </button>
        {r.status === "active" && (
          <button type="button" onClick={() => stop.mutate()} className="h-8 rounded-md px-3 hover:text-neg">
            Zatrzymaj
          </button>
        )}
      </div>
    </section>
  );
}

// Paper trading: zamrożona wersja strategii na żywych cenach, zanim dotknie prawdziwego konta.
export function PaperPage() {
  const q = useQuery({ queryKey: ["paper"], queryFn: api.paper, refetchInterval: 120_000 });
  const [open, setOpen] = useState<string | null>(null);
  const d = q.data;
  const selected = open ?? d?.runs[0]?.id ?? null;
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 sm:px-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold tracking-tight">Paper trading</h1>
        <p className="text-xs text-muted">
          Zamrożona strategia na żywych cenach, bez prawdziwych pieniędzy. Zielone światło dopiero po spełnieniu karty zamrożenia —
          nie po kilku dobrych dniach.
        </p>
      </div>
      {d && !d.bars_available && (
        <p className="rounded-md border border-line px-4 py-3 text-xs text-warn">
          Serwer nie pobiera świec M15 ({d.provider ? `dostawca ${d.provider} ich nie ma` : "brak dostawcy cen"}). Ustaw
          TAPE_PRICE_PROVIDER=oanda (konto demo) i uruchom worker <span className="num">python -m tape.paper</span> — bez tego
          przebiegi czekają na dane.
        </p>
      )}
      {d && d.bars_available && d.last_bar && (
        <p className="num text-xs text-muted">ostatnia świeca M15: {new Date(d.last_bar).toLocaleString("pl-PL")}</p>
      )}
      {d && d.runs.length > 0 && (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Przebiegi">
          {d.runs.map((r) => (
            <button key={r.id} type="button" role="tab" aria-selected={selected === r.id} onClick={() => setOpen(r.id)}
              className={`h-8 rounded-md border px-3 text-xs ${selected === r.id ? "border-fg" : "border-line text-muted hover:text-fg"}`}>
              {r.name} · {r.progress.trades}/{r.progress.need_trades}
            </button>
          ))}
        </div>
      )}
      {selected && <RunDetail id={selected} />}
      {d && <NewRun d={d} />}
    </div>
  );
}
