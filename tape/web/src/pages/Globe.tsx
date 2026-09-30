import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, type AssetBias, type MarketEvent } from "../api";
import { CalendarPanel } from "../components/CalendarPanel";
import { EventGlobe } from "../components/EventGlobe";
import { age, money } from "../format";

const LAYERS: { id: MarketEvent["category"]; label: string; dot: string }[] = [
  { id: "conflict", label: "Konflikty", dot: "#f2555a" },
  { id: "cb", label: "Banki centralne", dot: "#6e8bff" },
  { id: "macro", label: "Makro", dot: "#e5a93b" },
  { id: "supply", label: "Podaż", dot: "#2fbf71" },
  { id: "market", label: "Rynki", dot: "#8a8a93" },
];

function toneOf(v: number): string {
  return v > 0 ? "text-pos" : v < 0 ? "text-neg" : "text-muted";
}

function BiasRow({ asset, bias }: { asset: string; bias: AssetBias }) {
  const neutral = bias.label === "neutral";
  const width = Math.max(2, Math.round(Math.abs(bias.score) * 80));
  const left = bias.score >= 0 ? 80 : 80 - width;
  const fill = neutral ? "bg-muted" : bias.score > 0 ? "bg-pos" : "bg-neg";
  return (
    <div className="grid grid-cols-[40px_160px_1fr] items-center gap-3">
      <span className="num font-medium">{asset}</span>
      <div className="relative h-2 rounded bg-surface-2" role="meter" aria-valuemin={-1} aria-valuemax={1} aria-valuenow={bias.score} aria-label={`Nastawienie ${asset}`}>
        <div className="absolute -top-[3px] left-[79px] h-3.5 w-px bg-[#34343a]" />
        <div className={`absolute top-0 h-2 rounded ${fill}`} style={{ left, width }} />
      </div>
      <span className={`num whitespace-nowrap ${neutral ? "text-muted" : toneOf(bias.score)}`}>
        {money(bias.score, 2)} {neutral ? "" : `${bias.label} · `}
        {bias.strength}
      </span>
    </div>
  );
}

export function GlobePage() {
  const events = useQuery({ queryKey: ["events"], queryFn: api.events, refetchInterval: 60_000 });
  const bias = useQuery({ queryKey: ["bias"], queryFn: api.bias, refetchInterval: 60_000 });
  const [asset, setAsset] = useState<"XAU" | "XAG">("XAU");
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const visible = useMemo(() => (events.data ?? []).filter((e) => !hidden.has(e.category)), [events.data, hidden]);
  const sorted = useMemo(() => [...visible].sort((a, b) => a.age_minutes - b.age_minutes), [visible]);
  const selected = (events.data ?? []).find((e) => e.id === selectedId) ?? sorted.find((e) => e.age_minutes >= 0) ?? sorted[0];
  const assetBias = bias.data?.[asset];

  const arrow = (d: number | undefined) => (d == null || d === 0 ? "—" : d > 0 ? "▲" : "▼");

  return (
    <div className="flex h-[calc(100vh-48px)] min-h-[640px] flex-col lg:flex-row">
      <section aria-label="Globus zdarzeń" className="flex min-h-[420px] min-w-0 flex-1 flex-col border-b border-line lg:border-b-0 lg:border-r">
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line-soft px-4 py-2">
          {LAYERS.map((l) => {
            const on = !hidden.has(l.id);
            return (
              <button
                key={l.id}
                type="button"
                aria-pressed={on}
                onClick={() => {
                  const next = new Set(hidden);
                  if (on) next.add(l.id);
                  else next.delete(l.id);
                  setHidden(next);
                }}
                className={`flex h-7 items-center gap-1.5 rounded-full border border-line px-2.5 text-xs ${on ? "bg-surface-2 text-fg" : "text-[#6a6a72]"}`}
              >
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: l.dot }} />
                {l.label}
              </button>
            );
          })}
          <div className="flex-1" />
          <div role="group" aria-label="Aktywo" className="num flex overflow-hidden rounded-md border border-line text-xs">
            {(["XAU", "XAG"] as const).map((a) => (
              <button key={a} type="button" aria-pressed={asset === a} onClick={() => setAsset(a)} className={`h-7 px-3 ${asset === a ? "bg-surface-2 text-fg" : "text-muted"}`}>
                {a}
              </button>
            ))}
          </div>
        </div>
        <div className="relative min-h-0 flex-1">
          <EventGlobe events={visible} asset={asset} selectedId={selected?.id ?? null} onSelect={setSelectedId} />
          <div className="pointer-events-none absolute bottom-3 left-4 flex flex-col gap-1.5 text-xs text-muted">
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-pos" />byczo dla {asset === "XAU" ? "złota" : "srebra"}</span>
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-neg" />niedźwiedzio</span>
            <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-muted" />neutralnie · oczekiwane</span>
            <span>wielkość = waga · pierścień = ostatnia godzina</span>
          </div>
          {events.data?.some((e) => e.sample) && (
            <span className="absolute right-4 top-3 rounded border border-line bg-surface px-2 py-1 text-xs text-muted">Dane przykładowe</span>
          )}
        </div>
      </section>

      <aside aria-label="Analiza AI" className="flex w-full flex-col lg:w-[440px]">
        <section aria-label="Nastawienie newsów" className="flex flex-col gap-3 border-b border-line px-4 py-3.5">
          <h2 className="text-[13px] font-medium">Nastawienie newsów · 1–5 dni</h2>
          {bias.data ? (
            <>
              <BiasRow asset="XAU" bias={bias.data.XAU} />
              <BiasRow asset="XAG" bias={bias.data.XAG} />
            </>
          ) : (
            <span className="text-muted">{bias.isError ? "Nie udało się pobrać nastawienia." : "Ładowanie…"}</span>
          )}
          {assetBias && assetBias.drivers.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <span className="text-muted">Główne czynniki ({asset})</span>
              {assetBias.drivers.map((d) => (
                <button key={d.event_id} type="button" onClick={() => setSelectedId(d.event_id)} className="flex justify-between gap-3 text-left hover:text-white">
                  <span className="truncate">{d.title}</span>
                  <span className={`num ${toneOf(d.contribution)}`}>{money(d.contribution, 1)}</span>
                </button>
              ))}
            </div>
          )}
          <p className="rounded-md border border-line bg-surface px-2.5 py-2 leading-relaxed text-muted">
            {!bias.data?.track_record.available && <span className="text-warn">Trafność jeszcze niepoliczona. </span>}
            {bias.data?.track_record.available && !bias.data.track_record.labels_allowed && <span className="text-warn">Jeszcze nieistotne statystycznie. </span>}
            {bias.data?.track_record.note} To analiza newsów, nie sygnał transakcyjny.
          </p>
        </section>

        <CalendarPanel />

        <section aria-label="Zdarzenia" className="flex min-h-0 flex-1 flex-col border-b border-line">
          <div className="flex items-center border-b border-line-soft px-4 py-2">
            <h2 className="text-xs font-medium">Zdarzenia</h2>
            <div className="flex-1" />
            <span className="num text-xs text-muted">{visible.length} na mapie</span>
          </div>
          <ul className="min-h-0 flex-1 overflow-y-auto">
            {sorted.map((e) => {
              const d = e.impacts[asset]?.direction ?? 0;
              return (
                <li key={e.id}>
                  <button
                    type="button"
                    aria-pressed={selected?.id === e.id}
                    onClick={() => setSelectedId(e.id)}
                    className={`grid w-full grid-cols-[10px_1fr_56px_56px] items-center gap-2.5 border-b border-[#141417] px-4 py-2 text-left text-xs ${selected?.id === e.id ? "bg-surface-2" : "hover:bg-surface"}`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${d > 0 ? "bg-pos" : d < 0 ? "bg-neg" : "bg-muted"}`} />
                    <span className="truncate">{e.title}</span>
                    <span className={`num text-right ${toneOf(d)}`}>{arrow(d)} {asset}</span>
                    <span className="num text-right text-muted">{age(e.age_minutes)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>

        {selected && (
          <section aria-label="Szczegóły zdarzenia" className="flex flex-col gap-2 px-4 py-3.5">
            <div className="num flex gap-2 text-[11px] text-muted">
              <span>{selected.place}</span>·<span>{age(selected.age_minutes)}</span>·<span>{selected.sources ? `${selected.sources} źródeł` : "kalendarz"}</span>
            </div>
            <h3 className="text-sm font-medium leading-snug">{selected.title}</h3>
            <p className="leading-relaxed text-muted">{selected.summary}</p>
            <div className="num flex gap-1.5">
              {(["XAU", "XAG"] as const).map((a) => (
                <span key={a} className={`rounded border border-line px-2 py-0.5 ${toneOf(selected.impacts[a]?.direction ?? 0)}`}>
                  {a} {arrow(selected.impacts[a]?.direction)}
                </span>
              ))}
            </div>
            {selected.analog && <p className="leading-relaxed text-muted">{selected.analog}</p>}
          </section>
        )}
      </aside>
    </div>
  );
}
