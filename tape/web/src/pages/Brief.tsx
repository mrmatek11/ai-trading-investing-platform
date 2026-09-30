import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Brief, type BriefAi, type BriefCalendar, type BriefResponse } from "../api";

const LEAN_CLASS: Record<BriefAi["outlook"][number]["lean"], string> = {
  byczo: "text-pos",
  neutralnie: "text-muted",
  niedźwiedzio: "text-neg",
};
const LEAN_ICON: Record<BriefAi["outlook"][number]["lean"], string> = { byczo: "▲", neutralnie: "■", niedźwiedzio: "▼" };
const safeUrl = (u: string) => (/^https?:\/\//.test(u) ? u : undefined);
const pct = (v: number | null) => (v === null ? "" : `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(2)}%`);

function FactRefs({ ids }: { ids: string[] }) {
  if (!ids.length) return null;
  return <span className="num ml-1.5 text-[10px] text-muted">{ids.join(" ")}</span>;
}

function EventCard({ c, note, id }: { c: BriefCalendar; note?: BriefAi["events"][number]; id?: string }) {
  return (
    <li className="flex flex-col gap-2 border-b border-line-soft px-4 py-3 last:border-b-0">
      <div className="flex items-baseline gap-2">
        <span className={`h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full ${c.impact === "high" ? "bg-neg" : "bg-warn"}`} title={c.impact} />
        <span className="num w-24 shrink-0 whitespace-nowrap text-muted">
          {c.when} {c.local}
        </span>
        <span className="min-w-0 flex-1 font-medium">{c.title}</span>
        {id && <span className="num text-[10px] text-muted">{id}</span>}
      </div>
      {(c.forecast || c.previous) && (
        <div className="num flex gap-4 pl-0 sm:pl-[7rem] text-xs text-muted">
          {c.forecast && <span>prognoza {c.forecast}</span>}
          {c.previous && <span>poprzednio {c.previous}</span>}
        </div>
      )}
      {note && (
        <div className="flex flex-col gap-1.5 pl-0 sm:pl-[7rem] text-[13px] leading-relaxed">
          <p>{note.why}</p>
          <p>
            <span className="mr-1.5 text-muted" aria-label="powyżej prognozy">⬆</span>
            {note.if_above}
          </p>
          <p>
            <span className="mr-1.5 text-muted" aria-label="poniżej prognozy">⬇</span>
            {note.if_below}
          </p>
        </div>
      )}
    </li>
  );
}

function BriefView({ b }: { b: Brief }) {
  const ai = b.ai;
  const calIds = b.facts.filter((f) => f.kind === "calendar" && !f.text.startsWith("Kalendarz: brak")).map((f) => f.id);
  const notes = new Map((ai?.events ?? []).map((e) => [e.fact, e]));
  const [showFacts, setShowFacts] = useState(false);
  return (
    <div className="flex flex-col gap-5">
      {ai?.headline && <p className="text-lg font-semibold leading-snug tracking-tight">{ai.headline}</p>}
      {b.quotes.length > 0 && (
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          {b.quotes.map((q) => (
            <span key={q.asset} className="num">
              <span className="text-muted">{q.asset}</span> {q.price.toFixed(2)}{" "}
              {q.change_24h !== null && <span className={q.change_24h >= 0 ? "text-pos" : "text-neg"}>{pct(q.change_24h)}</span>}
            </span>
          ))}
        </div>
      )}
      {ai?.what_decides ? (
        <section className="rounded-md border border-accent/50 bg-accent/5 px-4 py-3">
          <h2 className="mb-1 text-[11px] font-medium uppercase tracking-wider text-accent">O czym zdecyduje dzień</h2>
          <p className="leading-relaxed">
            {ai.what_decides}
            <FactRefs ids={ai.what_decides_facts} />
          </p>
        </section>
      ) : (
        <p className="rounded-md border border-line px-4 py-3 text-xs text-muted">{b.ai_error || "Brak komentarza AI."}</p>
      )}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <section aria-label="Kalendarz i scenariusze" className="min-w-0 rounded-md border border-line">
          <h2 className="border-b border-line-soft px-4 py-2 text-xs font-medium">Kalendarz USD i scenariusze</h2>
          {b.calendar.length === 0 ? (
            <p className="px-4 py-3 text-xs text-muted">Brak ważnych danych USD na dziś — dzień bez zaplanowanego katalizatora.</p>
          ) : (
            <ul>
              {b.calendar.map((c, i) => (
                <EventCard key={c.ts + c.title} c={c} id={calIds[i]} note={calIds[i] ? notes.get(calIds[i]) : undefined} />
              ))}
            </ul>
          )}
        </section>

        <div className="flex min-w-0 flex-col gap-5">
          {ai && ai.outlook.length > 0 && (
            <section aria-label="Nastawienie" className="rounded-md border border-line">
              <h2 className="border-b border-line-soft px-4 py-2 text-xs font-medium">Nastawienie AI</h2>
              <ul>
                {ai.outlook.map((o) => (
                  <li key={o.asset} className="flex flex-col gap-1 border-b border-line-soft px-4 py-3 last:border-b-0">
                    <span className={`font-medium ${LEAN_CLASS[o.lean]}`}>
                      <span aria-hidden className="mr-1.5 text-[10px]">{LEAN_ICON[o.lean]}</span>
                      {o.asset} · {o.lean}
                    </span>
                    <p className="text-[13px] leading-relaxed">
                      {o.reasoning}
                      <FactRefs ids={o.facts} />
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {ai && ai.drivers.length > 0 && (
            <section aria-label="Czynniki" className="rounded-md border border-line">
              <h2 className="border-b border-line-soft px-4 py-2 text-xs font-medium">Co porusza rynkiem</h2>
              <ul>
                {ai.drivers.map((d) => (
                  <li key={d.title} className="flex flex-col gap-1 border-b border-line-soft px-4 py-3 last:border-b-0">
                    <span className="font-medium">{d.title}</span>
                    <p className="text-[13px] leading-relaxed text-muted">
                      {d.detail}
                      <FactRefs ids={d.facts} />
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section aria-label="Nagłówki" className="rounded-md border border-line">
            <h2 className="border-b border-line-soft px-4 py-2 text-xs font-medium">Nagłówki · ostatnie 18 h</h2>
            {b.headlines.length === 0 ? (
              <p className="px-4 py-3 text-xs text-muted">Brak nagłówków — uruchom pipeline newsów (RSS / GDELT).</p>
            ) : (
              <ul>
                {b.headlines.map((h) => (
                  <li key={h.url} className="flex gap-3 border-b border-line-soft px-4 py-2 text-[13px] last:border-b-0">
                    <span className="num shrink-0 text-muted">{h.local}</span>
                    <span className="min-w-0 flex-1">
                      <a href={safeUrl(h.url)} target="_blank" rel="noopener noreferrer" className="hover:underline">
                        {h.title}
                      </a>{" "}
                      <span className="text-xs text-muted">{h.source}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      {ai?.risk && <p className="text-xs text-warn">⚠ {ai.risk}</p>}
      <div className="flex flex-col gap-2 text-xs text-muted">
        <p>
          To nie jest rekomendacja inwestycyjna. Fakty (F1…) liczy kod z kalendarza, cen i nagłówków; AI tylko je interpretuje.
          {ai && ai.dropped > 0 && ` Odrzucone wnioski z liczbami spoza faktów: ${ai.dropped}.`}
        </p>
        <button type="button" onClick={() => setShowFacts((v) => !v)} className="w-fit text-left underline-offset-2 hover:underline" aria-expanded={showFacts}>
          {showFacts ? "Ukryj fakty" : `Pokaż fakty (${b.facts.length})`}
        </button>
        {showFacts && (
          <ol className="num flex flex-col gap-1 rounded-md border border-line-soft p-3">
            {b.facts.map((f) => (
              <li key={f.id}>
                <span className="text-fg">{f.id}</span> {f.text}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function DeliveryCard({ d }: { d: BriefResponse }) {
  const qc = useQueryClient();
  const [hook, setHook] = useState("");
  const refresh = () => qc.invalidateQueries({ queryKey: ["brief"] });
  const link = useMutation({ mutationFn: api.telegramLink, onSuccess: (r) => window.open(r.url, "_blank", "noopener") });
  const unlink = useMutation({ mutationFn: api.telegramUnlink, onSuccess: refresh });
  const saveHook = useMutation<unknown, Error, string>({
    mutationFn: (url) => api.saveBriefSubscription({ enabled: true, discord_webhook: url }),
    onSuccess: () => {
      setHook("");
      refresh();
    },
  });
  const sub = d.subscription;
  return (
    <section aria-label="Dostarczanie briefu" className="flex flex-col gap-4 rounded-md border border-line p-5">
      <div className="flex items-baseline gap-3">
        <h2 className="text-[13px] font-medium">Brief na telefon</h2>
        <span className="text-xs text-muted">
          dni robocze {d.config.time} ({d.config.tz})
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <span className="w-20 text-muted">Telegram</span>
        {!d.config.telegram || !d.config.bot_username ? (
          <span className="text-xs text-muted">bot nie jest skonfigurowany na serwerze</span>
        ) : sub.telegram_linked ? (
          <>
            <span className="text-pos">połączono{sub.telegram_name && ` · @${sub.telegram_name}`}</span>
            <button type="button" onClick={() => unlink.mutate()} className="h-8 rounded-md px-3 text-muted hover:text-neg">
              Odłącz
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={() => link.mutate()} disabled={link.isPending} className="h-8 rounded-md bg-fg px-3 font-medium text-bg disabled:opacity-40">
              Połącz z Telegramem
            </button>
            <span className="text-xs text-muted">otworzy @{d.config.bot_username} — naciśnij Start (link ważny 15 min)</span>
          </>
        )}
        {d.config.channel && (
          <a href={safeUrl(d.config.channel)} target="_blank" rel="noopener noreferrer" className="text-xs underline-offset-2 hover:underline">
            albo dołącz do kanału
          </a>
        )}
      </div>
      <form
        className="flex flex-wrap items-center gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          saveHook.mutate(hook.trim());
        }}
      >
        <span className="w-20 text-muted">Discord</span>
        {sub.discord_linked && !hook ? (
          <>
            <span className="text-pos">webhook podłączony</span>
            <button type="button" onClick={() => saveHook.mutate("")} className="h-8 rounded-md px-3 text-muted hover:text-neg">
              Odłącz
            </button>
          </>
        ) : !d.config.discord ? (
          <span className="text-xs text-muted">serwer nie ma klucza szyfrowania (TAPE_SECRET_KEYS)</span>
        ) : (
          <>
            <input type="password" value={hook} onChange={(e) => setHook(e.target.value)} autoComplete="off" spellCheck={false}
              placeholder="https://discord.com/api/webhooks/…" aria-label="Webhook Discord"
              className="num h-8 min-w-0 flex-1 rounded-md border border-line bg-surface px-2 placeholder:text-muted" />
            <button type="submit" disabled={!hook.trim() || saveHook.isPending} className="h-8 rounded-md bg-fg px-3 font-medium text-bg disabled:opacity-40">
              {saveHook.isPending ? "Wysyłam test…" : "Podłącz"}
            </button>
          </>
        )}
      </form>
      {(saveHook.isError || link.isError) && (
        <p role="alert" className="text-xs text-neg">
          {(saveHook.error ?? link.error)?.message}
        </p>
      )}
      <p className="text-xs text-muted">
        Webhook: kanał na Discordzie → Edytuj kanał → Integracje → Webhooki → Nowy webhook → Kopiuj URL. Zapisujemy go zaszyfrowanego i wysyłamy
        wiadomość testową.
      </p>
    </section>
  );
}

// Poranny brief: co dziś ważnego dla złota i srebra — w stylu kanału na Telegramie, z komentarzem AI.
export function BriefPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["brief"], queryFn: api.brief, refetchInterval: 300_000 });
  const gen = useMutation<unknown, Error>({ mutationFn: api.generateBrief, onSuccess: () => qc.invalidateQueries({ queryKey: ["brief"] }) });
  const b = q.data?.brief;
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-6 sm:px-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold tracking-tight">Brief dnia</h1>
        {b && (
          <span className="num text-xs text-muted">
            {b.day} · {new Date(b.created_at).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })}
            {b.model && ` · ${b.model}`}
          </span>
        )}
        <div className="flex-1" />
        <button type="button" onClick={() => gen.mutate()} disabled={gen.isPending} className="h-8 rounded-md border border-line px-3 text-xs disabled:opacity-40">
          {gen.isPending ? "Generuję…" : "Wygeneruj teraz"}
        </button>
      </div>
      {gen.isError && <p role="alert" className="text-xs text-neg">{gen.error.message}</p>}
      {q.isLoading ? (
        <p className="text-muted">Ładuję…</p>
      ) : b ? (
        <BriefView b={b} />
      ) : (
        <p className="rounded-md border border-line px-4 py-6 text-muted">
          Pierwszy brief pojawi się w najbliższy dzień roboczy o {q.data?.config.time}. Worker: <span className="num">python -m tape.brief</span>.
        </p>
      )}
      {q.data && <DeliveryCard d={q.data} />}
    </div>
  );
}
