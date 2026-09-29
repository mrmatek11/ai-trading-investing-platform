import type { Analytics, Summary } from "../../api";
import { money, num, pct, tone } from "../../format";
import { DailyCalendar, ZeroBars, ZeroLine } from "./charts";

const WD = ["Pn", "Wt", "Śr", "Cz", "Pt", "Sb", "Nd"];

// oś: dolna krawędź przedziału („−1…−0.5” → „−1”), skrajne przedziały bez zmian
const rLabel = (l: string) => (l.includes("…") ? l.split("…")[0] : l).replace("-", "−");

function Metric({ label, value, sub, className = "" }: { label: string; value: string; sub?: string; className?: string }) {
  return (
    <div className="flex flex-col gap-1 border-b border-r border-line px-4 py-3">
      <span className="text-xs text-muted">{label}</span>
      <span className={`num text-[17px] font-medium ${className}`}>{value}</span>
      {sub && <span className="num text-[11px] text-muted">{sub}</span>}
    </div>
  );
}

function Card({ title, note, children, className = "" }: { title: string; note?: string; children: React.ReactNode; className?: string }) {
  return (
    <section aria-label={title} className={`min-w-0 rounded-md border border-line px-4 pb-3 pt-3 ${className}`}>
      <div className="mb-2 flex items-baseline gap-3">
        <h2 className="text-[13px] font-medium">{title}</h2>
        {note && <span className="text-xs text-muted">{note}</span>}
      </div>
      {children}
    </section>
  );
}

/** Widok analityka: czy przewaga istnieje, skąd pochodzi i jak bardzo boli po drodze. */
export function AnalystPanel({ a, s }: { a: Analytics; s: Summary }) {
  const margin = s.win_rate != null && a.breakeven_win_rate != null ? s.win_rate - a.breakeven_win_rate : null;
  const streak = a.streaks.current;
  return (
    <>
      <section aria-label="Analiza przewagi" className="grid grid-cols-2 overflow-hidden rounded-md border-l border-t border-line md:grid-cols-3 xl:grid-cols-6">
        <Metric label="Oczekiwana wartość" value={a.expectancy != null ? money(a.expectancy) : "—"} sub="USD na transakcję" className={tone(a.expectancy)} />
        <Metric label="Payoff (śr. zysk / śr. strata)" value={num(a.payoff)}
          sub={a.avg_win != null && a.avg_loss != null ? `${money(a.avg_win, 0)} / ${money(a.avg_loss, 0)}` : undefined} />
        <Metric label="Win rate vs próg rentowności" value={margin != null ? `${margin >= 0 ? "+" : "−"}${pct(Math.abs(margin))}` : "—"}
          sub={a.breakeven_win_rate != null ? `próg ${pct(a.breakeven_win_rate)} · masz ${pct(s.win_rate)}` : undefined} className={tone(margin)} />
        <Metric label="Najdłużej pod wodą" value={`${a.drawdown.longest_days} dni`}
          sub={a.drawdown.current < 0 ? `teraz ${money(a.drawdown.current, 0)} · ${a.drawdown.underwater_days} dni` : "na szczycie"} />
        <Metric label="Serie (maks.)" value={`${a.streaks.max_wins} W · ${a.streaks.max_losses} L`}
          sub={streak ? `bieżąca: ${Math.abs(streak)} ${streak > 0 ? "wygranych" : "strat"} z rzędu` : undefined} />
        <Metric label="Sharpe · Sortino (dni)" value={`${num(a.sharpe_daily)} · ${num(a.sortino_daily)}`}
          sub={`${a.green_days}/${a.trading_days} dni na plus`} />
      </section>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card title="Kalendarz wyniku" note="26 tygodni · intensywność = wielkość dnia" className="lg:col-span-3">
          <DailyCalendar days={a.daily} />
          <div className="num mt-2 flex gap-4 text-[11px] text-muted">
            <span>najlepszy dzień {a.best_day != null ? money(a.best_day) : "—"}</span>
            <span>najgorszy {a.worst_day != null ? money(a.worst_day) : "—"}</span>
          </div>
        </Card>
        <Card title="Krocząca oczekiwana wartość" note={`ostatnie ${a.rolling.window} transakcji`} className="lg:col-span-2">
          <ZeroLine points={a.rolling.points.map((p) => ({ t: p.t, v: p.expectancy }))} />
          <p className="mt-1 text-[11px] text-muted">Spadek pod zero po okresie nad zerem = sygnał do przeglądu, zanim zrobi to saldo.</p>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Wynik wg godziny otwarcia" note="UTC">
          <ZeroBars unit="USD" labelEvery={3}
            bars={a.by_hour.map((h) => ({ key: String(h.hour), label: String(h.hour).padStart(2, "0"), value: h.pnl, count: h.trades }))} />
        </Card>
        <Card title="Wynik wg dnia tygodnia">
          <ZeroBars unit="USD"
            bars={a.by_weekday.slice(0, 5).map((d) => ({ key: String(d.weekday), label: WD[d.weekday], value: d.pnl, count: d.trades }))} />
        </Card>
        <Card title="Rozkład wyniku w R" note={a.r_hist ? "ile transakcji w przedziale" : undefined}>
          {a.r_hist ? (
            <ZeroBars unit="transakcji" labelEvery={2}
              bars={a.r_hist.map((b) => ({ key: b.label, label: rLabel(b.label), tip: `${b.label} R`, value: b.count, color: b.mid < 0 ? "neg" : "pos" }))} />
          ) : (
            <p className="py-8 text-center text-xs text-muted">Potrzeba ≥ 10 transakcji ze stop lossem — wpisz SL w journalu.</p>
          )}
        </Card>
      </div>
    </>
  );
}
