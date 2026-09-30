import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { age, price, tone } from "../format";

// Pasek cen: pokazuje wiek notowania — przy nieświeżych cenach nie udaje „na żywo”.
export function Ticker() {
  const q = useQuery({ queryKey: ["quotes"], queryFn: api.quotes, refetchInterval: 60_000 });
  if (!q.data?.length) return null;
  return (
    <div className="num hidden items-center gap-4 text-[13px] md:flex" aria-label="Ceny metali">
      {q.data.map((x) => {
        const stale = x.age_minutes > 90;
        return (
          <span key={x.asset} className="flex items-baseline gap-1.5" title={`${x.provider} · ${new Date(x.ts).toLocaleString("pl-PL")}`}>
            <span className="text-muted">{x.asset}</span>
            <span className={stale ? "text-muted" : "text-fg"}>{price(String(x.price))}</span>
            {x.change_24h != null && (
              <span className={tone(x.change_24h)}>
                {x.change_24h > 0 ? "+" : x.change_24h < 0 ? "−" : ""}
                {(Math.abs(x.change_24h) * 100).toFixed(2).replace(".", ",")}%
              </span>
            )}
            <span className={`text-[11px] ${stale ? "text-warn" : "text-muted"}`}>{age(x.age_minutes)}</span>
          </span>
        );
      })}
    </div>
  );
}
