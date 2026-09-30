import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { api, type PositionDetail, type Setup } from "../api";
import { ImpactDot } from "../components/CalendarPanel";
import { TradeChart } from "../components/TradeChart";
import { money, price, r, tone, when } from "../format";

function duration(a: string, b: string | null): string {
  if (!b) return "otwarta";
  const min = Math.round((new Date(b).getTime() - new Date(a).getTime()) / 60000);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  return h < 48 ? `${h} h ${min % 60} min` : `${Math.round(h / 24)} dni`;
}

function Row({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-line-soft py-2 last:border-0">
      <span className="text-muted">{label}</span>
      <span className={`num ${className}`}>{value}</span>
    </div>
  );
}

function JournalPanel({ detail, setups, mistakeOptions }: { detail: PositionDetail; setups: Setup[]; mistakeOptions: string[] }) {
  const qc = useQueryClient();
  const key = detail.position.key;
  const j = detail.journal;
  const [setupId, setSetupId] = useState<number | null>(j?.setup_id ?? null);
  const [checklist, setChecklist] = useState<Record<string, boolean>>(j?.checklist ?? {});
  const [mistakes, setMistakes] = useState<string[]>(j?.mistakes ?? []);
  const [notes, setNotes] = useState(j?.notes ?? "");
  const [stop, setStop] = useState(j?.initial_stop ?? "");
  const [custom, setCustom] = useState("");
  const brokerStop = detail.position.initial_stop && !j?.initial_stop;

  useEffect(() => {
    setSetupId(j?.setup_id ?? null);
    setChecklist(j?.checklist ?? {});
    setMistakes(j?.mistakes ?? []);
    setNotes(j?.notes ?? "");
    setStop(j?.initial_stop ?? "");
  }, [key]); // stan formularza resetujemy tylko przy przejściu do innej transakcji

  const save = useMutation({
    mutationFn: () => api.saveJournal(key, { setup_id: setupId, checklist, mistakes, notes, initial_stop: stop ? Number(stop) : null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["position", key] });
      qc.invalidateQueries({ queryKey: ["positions"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["setups"] });
    },
  });
  const setup = setups.find((s) => s.id === setupId);
  const options = [...new Set([...mistakeOptions, ...mistakes])];
  const toggle = (m: string) => setMistakes(mistakes.includes(m) ? mistakes.filter((x) => x !== m) : [...mistakes, m]);

  return (
    <section aria-label="Journal" className="flex flex-col gap-4 rounded-md border border-line p-4">
      <h2 className="text-[13px] font-medium">Journal</h2>
      <label className="flex items-center justify-between gap-3">
        <span className="text-muted">Setup</span>
        <select value={setupId ?? ""} onChange={(e) => setSetupId(e.target.value ? Number(e.target.value) : null)} className="h-8 min-w-48 rounded-md border border-line bg-surface px-2">
          <option value="">— bez setupu —</option>
          {setups.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      {setups.length === 0 && (
        <p className="text-xs text-muted">
          Nie masz jeszcze setupów. <Link to="/playbooks" className="text-accent">Dodaj playbook →</Link>
        </p>
      )}
      {setup && setup.rules.length > 0 && (
        <fieldset className="flex flex-col gap-1.5">
          <legend className="mb-1 text-muted">Checklista „{setup.name}”</legend>
          {setup.rules.map((rule) => (
            <label key={rule} className="flex items-center gap-2">
              <input type="checkbox" checked={!!checklist[rule]} onChange={(e) => setChecklist({ ...checklist, [rule]: e.target.checked })} className="accent-[var(--color-accent)]" />
              <span className={checklist[rule] ? "" : "text-muted"}>{rule}</span>
            </label>
          ))}
        </fieldset>
      )}

      <fieldset>
        <legend className="mb-1.5 text-muted">Błędy</legend>
        <div className="flex flex-wrap gap-1.5">
          {options.map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mistakes.includes(m)}
              onClick={() => toggle(m)}
              className={`h-7 rounded-full border px-2.5 text-xs ${mistakes.includes(m) ? "border-neg text-neg" : "border-line text-muted hover:text-fg"}`}
            >
              {m}
            </button>
          ))}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (custom.trim() && !mistakes.includes(custom.trim())) setMistakes([...mistakes, custom.trim()]);
              setCustom("");
            }}
          >
            <label className="sr-only" htmlFor="custom-mistake">Własny błąd</label>
            <input id="custom-mistake" value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="+ własny" className="h-7 w-28 rounded-full border border-dashed border-line bg-transparent px-2.5 text-xs" />
          </form>
        </div>
      </fieldset>

      <label className="flex flex-col gap-1.5">
        <span className="text-muted">Początkowy stop loss {brokerStop ? "(z pliku brokera)" : "(wpisz, jeśli broker go nie podał — policzymy R)"}</span>
        <input
          type="number"
          step="any"
          inputMode="decimal"
          value={brokerStop ? detail.position.initial_stop ?? "" : stop}
          disabled={!!brokerStop}
          onChange={(e) => setStop(e.target.value)}
          className="num h-8 rounded-md border border-line bg-surface px-2 disabled:text-muted"
        />
      </label>

      <label className="flex flex-col gap-1.5">
        <span className="text-muted">Notatka</span>
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={5} className="resize-y rounded-md border border-line bg-surface px-2.5 py-2 leading-relaxed" placeholder="Dlaczego wszedłeś? Co byś zrobił inaczej?" />
      </label>

      <div className="flex items-center gap-3">
        {save.isError && <span role="alert" className="text-neg">{save.error.message}</span>}
        {save.isSuccess && !save.isPending && <span role="status" className="text-pos">Zapisano</span>}
        <div className="flex-1" />
        <button type="button" onClick={() => save.mutate()} disabled={save.isPending} className="h-9 rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40">
          {save.isPending ? "Zapisuję…" : "Zapisz"}
        </button>
      </div>
    </section>
  );
}

export function TradeDetail() {
  const { key } = useParams({ strict: false }) as { key: string };
  const detail = useQuery({ queryKey: ["position", key], queryFn: () => api.position(key) });
  const setups = useQuery({ queryKey: ["setups", "all"], queryFn: () => api.setups() });
  const meta = useQuery({ queryKey: ["journal-meta"], queryFn: api.journalMeta, staleTime: Infinity });

  if (detail.isLoading) return <div className="p-6 text-muted">Ładowanie…</div>;
  if (detail.isError || !detail.data) return <div className="p-6 text-neg">Nie znaleziono transakcji. <Link to="/trades" className="text-accent">Wróć do listy</Link></div>;
  const d = detail.data;
  const p = d.position;

  return (
    <div className="flex flex-col gap-4 px-6 py-5">
      <header className="flex flex-wrap items-center gap-3">
        <Link to="/trades" className="text-muted hover:text-fg">← Transakcje</Link>
        <span className="text-line">/</span>
        <h1 className="num text-base font-medium">{p.symbol}</h1>
        <span className="rounded border border-line px-2 py-0.5 text-xs">{p.direction === "long" ? "Long" : "Short"}</span>
        <span className="text-xs text-muted">
          {when(p.opened_at)} → {p.closed_at ? when(p.closed_at) : "otwarta"} · {duration(p.opened_at, p.closed_at)}
        </span>
        <div className="flex-1" />
        <span className={`num text-xl font-medium ${tone(p.r_multiple)}`}>{r(p.r_multiple)}</span>
        <span className={`num text-xl font-medium ${tone(p.net_pnl)}`}>{money(p.net_pnl)}</span>
      </header>

      <section aria-label="Wykres" className="rounded-md border border-line px-3 pt-3">
        <TradeChart detail={d} />
        {d.prices.length === 0 && (
          <p className="px-1 pb-3 text-xs text-muted">Brak zapisanych cen dla {p.symbol} — wykres pokazuje tylko ceny fill-i. Ceny dodasz przez /api/prices.</p>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <section aria-label="Liczby" className="rounded-md border border-line p-4">
          <h2 className="mb-2 text-[13px] font-medium">Liczby</h2>
          <Row label="Wejście (średnie)" value={price(p.avg_entry)} />
          <Row label="Wyjście (średnie)" value={price(p.avg_exit)} />
          <Row label="Stop loss" value={price(p.initial_stop)} />
          <Row label="Ilość" value={p.qty} />
          <Row label="Koszty" value={money(p.fees)} className={tone(p.fees)} />
          <Row label="R" value={r(p.r_multiple)} className={tone(p.r_multiple)} />
          <Row label="PnL netto" value={money(p.net_pnl)} className={tone(p.net_pnl)} />
        </section>

        <section aria-label="Wykonania" className="rounded-md border border-line p-4">
          <h2 className="mb-2 text-[13px] font-medium">Wykonania</h2>
          <table className="w-full text-[12px]">
            <thead className="text-muted">
              <tr>
                <th className="py-1 text-left font-normal">Czas</th>
                <th className="py-1 text-left font-normal">Strona</th>
                <th className="py-1 text-right font-normal">Ilość</th>
                <th className="py-1 text-right font-normal">Cena</th>
              </tr>
            </thead>
            <tbody>
              {d.fills.map((f) => (
                <tr key={f.id} className="border-t border-line-soft">
                  <td className="num py-1.5 text-muted">{when(f.ts)}</td>
                  <td className="py-1.5">{f.side === "buy" ? "Kupno" : "Sprzedaż"}</td>
                  <td className="num py-1.5 text-right">{f.qty}</td>
                  <td className="num py-1.5 text-right">{price(f.price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {d.events.length > 0 && (
          <section aria-label="Dane makro w pobliżu" className="rounded-md border border-line p-4">
            <h2 className="mb-2 text-[13px] font-medium">Dane makro w pobliżu</h2>
            <ul className="flex flex-col gap-1.5 text-[12px]">
              {d.events.map((e) => {
                const mins = Math.round((new Date(e.ts).getTime() - new Date(p.opened_at).getTime()) / 60000);
                const rel = mins === 0 ? "przy wejściu" : mins > 0 ? `${mins} min po wejściu` : `${-mins} min przed wejściem`;
                return (
                  <li key={e.ts + e.title} className="flex items-center gap-2">
                    <ImpactDot impact={e.impact} />
                    <span className="min-w-0 flex-1 truncate">{e.title}</span>
                    <span className={`num ${Math.abs(mins) <= 30 ? "text-warn" : "text-muted"}`}>{rel}</span>
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        <JournalPanel detail={d} setups={setups.data ?? []} mistakeOptions={meta.data?.mistakes ?? []} />
      </div>
    </div>
  );
}
