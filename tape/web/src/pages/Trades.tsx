import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { money, r, tone, when } from "../format";

export function Trades() {
  const q = useQuery({ queryKey: ["positions"], queryFn: api.positions });
  return (
    <div className="px-6 py-5">
      <h1 className="mb-4 text-xl font-semibold tracking-tight">Transakcje</h1>
      {q.isError && <p className="text-neg">Nie udało się pobrać transakcji.</p>}
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="w-full min-w-[760px] text-[12px]">
          <thead className="text-muted">
            <tr className="border-b border-line">
              {["Otwarcie", "Zamknięcie", "Symbol", "Strona", "Ilość", "Wejście", "Wyjście", "SL", "Koszty", "R", "PnL"].map((h, i) => (
                <th key={h} className={`px-3 py-2 font-normal ${i >= 4 ? "text-right" : "text-left"}`}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(q.data ?? []).map((p, i) => (
              <tr key={i} className="border-b border-line-soft last:border-0 hover:bg-surface">
                <td className="num px-3 py-2 text-muted">{when(p.opened_at)}</td>
                <td className="num px-3 py-2 text-muted">{p.closed_at ? when(p.closed_at) : "otwarta"}</td>
                <td className="num px-3 py-2">{p.symbol}</td>
                <td className="px-3 py-2 text-muted">{p.direction === "long" ? "Long" : "Short"}</td>
                <td className="num px-3 py-2 text-right">{p.qty}</td>
                <td className="num px-3 py-2 text-right">{p.avg_entry}</td>
                <td className="num px-3 py-2 text-right">{p.avg_exit ?? "—"}</td>
                <td className="num px-3 py-2 text-right text-muted">{p.initial_stop ?? "—"}</td>
                <td className={`num px-3 py-2 text-right ${tone(p.fees)}`}>{money(p.fees)}</td>
                <td className={`num px-3 py-2 text-right ${tone(p.r_multiple)}`}>{r(p.r_multiple)}</td>
                <td className={`num px-3 py-2 text-right ${tone(p.net_pnl)}`}>{money(p.net_pnl)}</td>
              </tr>
            ))}
            {q.data?.length === 0 && (
              <tr>
                <td colSpan={11} className="px-3 py-8 text-center text-muted">
                  Brak transakcji — zaimportuj plik w zakładce Import.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
