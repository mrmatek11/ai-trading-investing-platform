import { useState } from "react";
import type { Mapping, SuggestResponse } from "../api";

const FIELDS: { id: string; label: string; required: boolean }[] = [
  { id: "id", label: "ID transakcji", required: true },
  { id: "time", label: "Czas", required: true },
  { id: "symbol", label: "Symbol", required: true },
  { id: "side", label: "Strona (kupno/sprzedaż)", required: true },
  { id: "qty", label: "Ilość", required: true },
  { id: "price", label: "Cena", required: true },
  { id: "fee", label: "Koszty", required: false },
  { id: "pnl", label: "Zrealizowany wynik", required: false },
  { id: "stop_loss", label: "Stop loss", required: false },
  { id: "contract_size", label: "Wielkość kontraktu", required: false },
];

const input = "num h-8 rounded-md border border-line bg-surface px-2 text-fg";

type Props = {
  data: SuggestResponse;
  pending: boolean;
  onSubmit: (m: Mapping) => void;
  onCancel: () => void;
};

export function MappingStep({ data, pending, onSubmit, onCancel }: Props) {
  const s = data.suggestion;
  const [columns, setColumns] = useState<Record<string, string>>(s.columns);
  const [tz, setTz] = useState(s.tz || "UTC");
  const [dateFormat, setDateFormat] = useState(s.date_format ?? "");
  const [buy, setBuy] = useState((s.buy_values ?? ["buy", "kupno"]).join(", "));
  const [sell, setSell] = useState((s.sell_values ?? ["sell", "sprzedaż"]).join(", "));
  const missing = FIELDS.filter((f) => f.required && !columns[f.id]);
  const split = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);

  return (
    <section aria-label="Mapowanie kolumn" className="flex flex-col gap-4 rounded-md border border-line p-5">
      <div>
        <h2 className="text-[15px] font-medium">Mapowanie kolumn</h2>
        <p className="mt-1 text-muted">
          Nie rozpoznaliśmy formatu, więc proponujemy dopasowanie
          {s.source === "ai" ? " (AI)" : data.ai_available ? " (heurystyka)" : " (heurystyka — AI nieskonfigurowane)"}. Sprawdź i popraw przed importem.
          {s.notes && ` ${s.notes}`}
        </p>
      </div>

      <div className="grid gap-x-5 gap-y-2 sm:grid-cols-2">
        {FIELDS.map((f) => {
          const conf = s.confidence[f.id];
          return (
            <label key={f.id} className="grid grid-cols-[minmax(0,1fr)_180px] items-center gap-3">
              <span>
                {f.label}
                {f.required && <span className="text-muted"> *</span>}
                {conf != null && conf < 0.8 && columns[f.id] === s.columns[f.id] && <span className="text-warn"> · sprawdź</span>}
              </span>
              <select
                value={columns[f.id] ?? ""}
                onChange={(e) => {
                  const next = { ...columns };
                  if (e.target.value) next[f.id] = e.target.value;
                  else delete next[f.id];
                  setColumns(next);
                }}
                className={`${input} ${f.required && !columns[f.id] ? "border-warn" : ""}`}
              >
                <option value="">—</option>
                {data.headers.map((h) => (
                  <option key={h} value={h}>
                    {h}
                  </option>
                ))}
              </select>
            </label>
          );
        })}
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        <label className="flex flex-col gap-1.5"><span className="text-muted">Strefa czasowa</span><input value={tz} onChange={(e) => setTz(e.target.value)} className={input} /></label>
        <label className="flex flex-col gap-1.5"><span className="text-muted">Format daty (opcjonalnie)</span><input value={dateFormat} onChange={(e) => setDateFormat(e.target.value)} placeholder="%Y-%m-%d %H:%M:%S" className={input} /></label>
        <label className="flex flex-col gap-1.5"><span className="text-muted">Wartości „kupno”</span><input value={buy} onChange={(e) => setBuy(e.target.value)} className={input} /></label>
        <label className="flex flex-col gap-1.5"><span className="text-muted">Wartości „sprzedaż”</span><input value={sell} onChange={(e) => setSell(e.target.value)} className={input} /></label>
      </div>

      <div className="overflow-x-auto rounded-md border border-line">
        <table className="w-full text-[12px]">
          <thead className="text-muted">
            <tr className="border-b border-line">
              {data.headers.map((h) => {
                const field = Object.entries(columns).find(([, c]) => c === h)?.[0];
                return (
                  <th key={h} className="whitespace-nowrap px-3 py-2 text-left font-normal">
                    {h}
                    {field && <span className="block text-accent">→ {FIELDS.find((f) => f.id === field)?.label}</span>}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {data.preview.map((row, i) => (
              <tr key={i} className="border-b border-line-soft last:border-0">
                {data.headers.map((h) => (
                  <td key={h} className="num whitespace-nowrap px-3 py-1.5">{row[h] ?? ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">Podgląd {data.preview.length} z {data.rows} wierszy.</p>

      <div className="flex items-center gap-3">
        {missing.length > 0 && <span className="text-warn">Brakuje: {missing.map((f) => f.label).join(", ")}</span>}
        <div className="flex-1" />
        <button type="button" onClick={onCancel} className="h-9 rounded-md border border-line px-4">Anuluj</button>
        <button
          type="button"
          disabled={missing.length > 0 || pending}
          onClick={() => onSubmit({ columns, tz, date_format: dateFormat || null, buy_values: split(buy), sell_values: split(sell) })}
          className="h-9 rounded-md bg-fg px-4 font-medium text-bg disabled:opacity-40"
        >
          {pending ? "Importuję…" : "Importuj z tym mapowaniem"}
        </button>
      </div>
    </section>
  );
}
