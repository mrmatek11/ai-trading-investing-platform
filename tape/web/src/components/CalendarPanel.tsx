import { useQuery } from "@tanstack/react-query";
import { api, type EconEvent } from "../api";

const WD = new Intl.DateTimeFormat("pl-PL", { weekday: "short" });
const DAY = new Intl.DateTimeFormat("pl-PL", { day: "2-digit", month: "2-digit" });
const TIME = new Intl.DateTimeFormat("pl-PL", { hour: "2-digit", minute: "2-digit" });

export function eventLabel(e: EconEvent) {
  const d = new Date(e.ts);
  return `${WD.format(d).replace(".", "")} ${DAY.format(d)} ${TIME.format(d)}`;
}

function until(ts: string, now: number) {
  const m = Math.round((new Date(ts).getTime() - now) / 60000);
  if (m < -30) return "po";
  if (m <= 30) return "teraz";
  if (m < 120) return `za ${m} min`;
  if (m < 48 * 60) return `za ${Math.round(m / 60)} h`;
  return "";
}

export function ImpactDot({ impact }: { impact: EconEvent["impact"] }) {
  return <span title={impact} className={`h-1.5 w-1.5 shrink-0 rounded-full ${impact === "high" ? "bg-neg" : impact === "medium" ? "bg-warn" : "bg-muted"}`} />;
}

// Najbliższe dane USD — to one najczęściej ruszają złotem (Fed, inflacja, rynek pracy).
export function CalendarPanel() {
  const q = useQuery({ queryKey: ["calendar"], queryFn: () => api.calendar(7), refetchInterval: 300_000 });
  const now = Date.now();
  const events = q.data?.events ?? [];
  return (
    <section aria-label="Kalendarz makro" className="border-b border-line">
      <div className="flex items-center border-b border-line-soft px-4 py-2">
        <h2 className="text-xs font-medium">Kalendarz USD · 7 dni</h2>
        <div className="flex-1" />
        <span className="text-[11px] text-muted">czas lokalny</span>
      </div>
      {events.length === 0 ? (
        <p className="px-4 py-2.5 text-xs text-muted">Brak ważnych danych USD w najbliższych dniach.</p>
      ) : (
        <ul className="max-h-40 overflow-y-auto">
          {events.map((e) => {
            const u = until(e.ts, now);
            return (
              <li key={e.ts + e.title} className={`flex items-center gap-2.5 border-b border-[#141417] px-4 py-1.5 text-xs ${u === "po" ? "opacity-50" : ""}`}>
                <ImpactDot impact={e.impact} />
                <span className="num w-[112px] shrink-0 whitespace-nowrap text-muted">{eventLabel(e)}</span>
                <span className="min-w-0 flex-1 truncate">{e.title}</span>
                {e.forecast && <span className="num text-muted">prog. {e.forecast}</span>}
                {u && u !== "po" && <span className={`num ${u === "teraz" ? "text-warn" : "text-muted"}`}>{u}</span>}
              </li>
            );
          })}
        </ul>
      )}
      {q.data?.note && <p className="px-4 py-1.5 text-[11px] text-muted">{q.data.note}</p>}
    </section>
  );
}
