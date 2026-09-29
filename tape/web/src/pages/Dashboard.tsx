import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { api, type Segment } from "../api";
import { useBook } from "../book";
import { AiReview } from "../components/AiReview";
import { EquityChart } from "../components/EquityChart";
import { PropLimits } from "../components/PropLimits";
import { money, num, pct, r, tone, when } from "../format";

const GROUP_LABEL: Record<string, string> = {
  symbol: "Symbol",
  direction: "Kierunek",
  hour_utc: "Godzina otwarcia (UTC)",
  weekday: "Dzień tygodnia",
  after_loss: "Po stracie",
  news_window: "Wejście przy ważnych danych USD",
};

function Insight({ s }: { s: Segment }) {
  return (
    <li className="flex flex-col gap-1 border-b border-line-soft py-2.5 last:border-0">
      <span>
        {GROUP_LABEL[s.group] ?? s.group}: <span className="font-medium">{s.key}</span>
      </span>
      <span className="text-muted">
        <span className="num text-fg">{s.trades}</span> transakcji, średnio{" "}
        <span className={`num ${tone(s.avg_pnl)}`}>{money(s.avg_pnl)}</span>, łącznie{" "}
        <span className={`num ${tone(s.net_pnl)}`}>{money(s.net_pnl)}</span> · t ={" "}
        <span className="num">{num(s.t_vs_rest)}</span> względem reszty
      </span>
    </li>
  );
}

function Kpi({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="flex flex-col gap-1.5 border-r border-line px-4 py-3.5 last:border-r-0">
      <span className="text-xs text-muted">{label}</span>
      <span className={`num text-[22px] font-medium ${className}`}>{value}</span>
    </div>
  );
}

export function Dashboard() {
  const { book } = useBook();
  const stats = useQuery({ queryKey: ["stats", book], queryFn: () => api.stats(book) });
  const positions = useQuery({ queryKey: ["positions", book], queryFn: () => api.positions(book) });

  if (stats.isLoading) return <div className="p-6 text-muted">Ładowanie…</div>;
  if (stats.isError) return <div className="p-6 text-neg">Nie udało się pobrać statystyk: {String(stats.error)}</div>;
  const s = stats.data!.summary;

  if (s.trades === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <h1 className="text-xl font-semibold">Brak zamkniętych transakcji</h1>
        <p className="max-w-md text-muted">Zaimportuj historię z XTB, MetaTradera 5 albo dowolny plik CSV — statystyki i wnioski policzą się same.</p>
        <Link to="/import" className="rounded-md bg-fg px-4 py-2 font-medium text-bg">
          Importuj transakcje
        </Link>
      </div>
    );
  }

  const insights = stats.data!.segments.filter((x) => x.significant).slice(0, 4);
  return (
    <div className="flex flex-col gap-4 px-6 py-5">
      <h1 className="text-xl font-semibold tracking-tight">Journal</h1>

      <PropLimits />

      <section aria-label="Kluczowe liczby" className="grid grid-cols-2 rounded-md border border-line md:grid-cols-5">
        <Kpi label="Net PnL" value={money(s.net_pnl)} className={tone(s.net_pnl)} />
        <Kpi label="Win rate" value={pct(s.win_rate)} />
        <Kpi label={s.avg_r != null ? `Avg R · ${s.r_trades} z SL` : `Śr. PnL · ${s.trades} transakcji`} value={s.avg_r != null ? r(s.avg_r) : money(s.avg_pnl ?? 0)} />
        <Kpi label="Profit factor" value={num(s.profit_factor)} />
        <Kpi label="Max drawdown" value={money(s.max_drawdown)} className={s.max_drawdown < 0 ? "text-neg" : ""} />
      </section>

      <section aria-label="Krzywa kapitału" className="rounded-md border border-line px-4 pb-2 pt-3">
        <div className="mb-2 flex items-baseline gap-3">
          <h2 className="text-[13px] font-medium">Equity</h2>
          <span className="text-xs text-muted">
            t = <span className="num">{num(s.t_stat)}</span> {s.t_stat != null && Math.abs(s.t_stat) < 2 ? "· wynik jeszcze nieodróżnialny od szumu" : ""}
          </span>
        </div>
        <EquityChart points={stats.data!.equity} />
      </section>

      <AiReview />

      <div className="grid gap-4 lg:grid-cols-3">
        <section aria-label="Ostatnie transakcje" className="rounded-md border border-line lg:col-span-2">
          <div className="flex items-center border-b border-line px-4 py-3">
            <h2 className="text-[13px] font-medium">Ostatnie transakcje</h2>
            <div className="flex-1" />
            <Link to="/trades" className="text-xs text-muted hover:text-fg">
              Wszystkie →
            </Link>
          </div>
          <table className="w-full text-[12px]">
            <thead className="text-muted">
              <tr className="border-b border-line">
                <th className="px-4 py-2 text-left font-normal">Zamknięcie</th>
                <th className="px-2 py-2 text-left font-normal">Symbol</th>
                <th className="px-2 py-2 text-left font-normal">Strona</th>
                <th className="px-2 py-2 text-right font-normal">R</th>
                <th className="px-4 py-2 text-right font-normal">PnL</th>
              </tr>
            </thead>
            <tbody>
              {(positions.data ?? []).filter((p) => p.closed_at).slice(0, 8).map((p) => (
                <tr key={p.key} className="border-b border-line-soft last:border-0 hover:bg-surface">
                  <td className="num px-4 py-2 text-muted">
                    <Link to="/trades/$key" params={{ key: p.key }} className="hover:text-fg">{when(p.closed_at!)}</Link>
                  </td>
                  <td className="num px-2 py-2">{p.symbol}</td>
                  <td className="px-2 py-2 text-muted">{p.direction === "long" ? "Long" : "Short"}</td>
                  <td className={`num px-2 py-2 text-right ${tone(p.r_multiple)}`}>{r(p.r_multiple)}</td>
                  <td className={`num px-4 py-2 text-right ${tone(p.net_pnl)}`}>{money(p.net_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <div className="flex flex-col gap-4">
        <section aria-label="Wnioski" className="rounded-md border border-line px-4 py-3">
          <h2 className="mb-1 text-[13px] font-medium">Wnioski</h2>
          {insights.length ? (
            <ul>{insights.map((x) => <Insight key={x.group + x.key} s={x} />)}</ul>
          ) : (
            <p className="leading-relaxed text-muted">
              Brak istotnych różnic między segmentami (godzina, dzień, symbol, „po stracie”). Potrzeba co najmniej 10 transakcji w segmencie i |t| ≥ 2 — nie pokazujemy wniosków z szumu.
            </p>
          )}
        </section>

        <section aria-label="Wynik transakcji z błędami" className="rounded-md border border-line px-4 py-3">
          <h2 className="mb-1 text-[13px] font-medium">Wynik transakcji z błędami</h2>
          {stats.data!.mistakes.length ? (
            <ul>
              {stats.data!.mistakes.slice(0, 5).map((m) => (
                <li key={m.key} className="flex justify-between gap-3 border-b border-line-soft py-2 last:border-0">
                  <span className="truncate">{m.key} <span className="num text-muted">· {m.trades}</span></span>
                  <span className={`num ${tone(m.net_pnl)}`}>{money(m.net_pnl)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="leading-relaxed text-muted">Oznaczaj błędy w szczegółach transakcji — policzymy, ile każdy kosztuje.</p>
          )}
        </section>
        </div>
      </div>
    </div>
  );
}
