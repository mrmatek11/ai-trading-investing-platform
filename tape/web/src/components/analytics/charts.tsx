import { useMemo, useRef, useState, type ReactNode } from "react";
import { money } from "../../format";

// Małe wykresy SVG dashboardu. Znak wyniku zawsze kodujemy też położeniem (nad/pod osią zera)
// i znakiem w podpowiedzi — kolor zysk/strata nie jest jedynym nośnikiem informacji.

type Tip = { x: number; y: number; body: ReactNode } | null;

function useTip() {
  const [tip, setTip] = useState<Tip>(null);
  const box = useRef<HTMLDivElement>(null);
  const show = (e: React.MouseEvent, body: ReactNode) => {
    const r = box.current?.getBoundingClientRect();
    if (r) setTip({ x: e.clientX - r.left, y: e.clientY - r.top, body });
  };
  const layer = tip && (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 whitespace-nowrap rounded-md border border-line bg-surface-2 px-2.5 py-1.5 text-xs shadow-lg"
      style={{ left: Math.min(tip.x + 12, (box.current?.clientWidth ?? 300) - 150), top: Math.max(0, tip.y - 44) }}
    >
      {tip.body}
    </div>
  );
  return { box, show, hide: () => setTip(null), layer };
}

export type Bar = { key: string; label: string; tip?: string; value: number; count?: number; color?: "pos" | "neg" | "muted" };

/** Słupki od osi zera (w górę zysk, w dół strata). Etykiety osi X co `labelEvery`. */
export function ZeroBars({ bars, height = 140, labelEvery = 1, unit = "", countLabel = "transakcji" }: {
  bars: Bar[]; height?: number; labelEvery?: number; unit?: string; countLabel?: string;
}) {
  const { box, show, hide, layer } = useTip();
  const max = Math.max(1e-9, ...bars.map((b) => Math.abs(b.value)));
  const hasNeg = bars.some((b) => b.value < 0);
  const plotH = height - 18;
  const zero = hasNeg ? plotH / 2 : plotH;
  const scale = (hasNeg ? plotH / 2 - 4 : plotH - 4) / max;
  const w = 100 / bars.length;
  return (
    <div ref={box} className="relative min-w-0" onMouseLeave={hide}>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="block w-full" style={{ height }} role="img">
        <line x1="0" x2="100" y1={zero} y2={zero} stroke="var(--color-line)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {bars.map((b, i) => {
          const hgt = Math.max(b.value === 0 ? 0 : 1, Math.abs(b.value) * scale);
          const y = b.value >= 0 ? zero - hgt : zero;
          const fill = b.color === "muted" ? "var(--color-muted)" : (b.color ?? (b.value >= 0 ? "pos" : "neg")) === "pos" ? "var(--color-pos)" : "var(--color-neg)";
          return (
            <g key={b.key}>
              {/* pole trafienia większe niż słupek */}
              <rect x={i * w} y={0} width={w} height={plotH} fill="transparent"
                onMouseMove={(e) => show(e, <><div className="text-muted">{b.tip ?? b.label}</div><div className="num">{unit === "USD" ? money(b.value) : b.value}{unit && unit !== "USD" ? ` ${unit}` : unit === "USD" ? " USD" : ""}{b.count != null ? ` · ${b.count} ${countLabel}` : ""}</div></>)} />
              <rect x={i * w + w * 0.18} y={y} width={w * 0.64} height={hgt} fill={fill} opacity={b.count === 0 ? 0.25 : 0.9} pointerEvents="none" />
            </g>
          );
        })}
      </svg>
      <div className="num relative h-4 text-[10px] text-muted">
        {bars.map((b, i) =>
          i % labelEvery === 0 ? (
            <span key={b.key} className="absolute -translate-x-1/2 whitespace-nowrap" style={{ left: `${(i + 0.5) * w}%` }}>{b.label}</span>
          ) : null,
        )}
      </div>
      {layer}
    </div>
  );
}

/** Linia z osią zera (krocząca oczekiwana wartość). Crosshair + podpowiedź przy najechaniu. */
export function ZeroLine({ points, height = 140 }: { points: { t: string; v: number }[]; height?: number }) {
  const { box, show, hide, layer } = useTip();
  const [hover, setHover] = useState<number | null>(null);
  const { path, zero, ys } = useMemo(() => {
    const vals = points.map((p) => p.v);
    const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
    const span = hi - lo || 1;
    const y = (v: number) => 6 + (1 - (v - lo) / span) * (height - 12);
    const x = (i: number) => (points.length < 2 ? 50 : (i / (points.length - 1)) * 100);
    return { path: points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.v)}`).join(""), zero: y(0), ys: points.map((p) => y(p.v)) };
  }, [points, height]);
  if (points.length < 2) return <p className="py-6 text-center text-xs text-muted">Za mało transakcji na okno kroczące.</p>;
  return (
    <div ref={box} className="relative" onMouseLeave={() => { hide(); setHover(null); }}>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" className="block w-full" style={{ height }}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const i = Math.round(((e.clientX - r.left) / r.width) * (points.length - 1));
          const p = points[Math.max(0, Math.min(points.length - 1, i))];
          setHover(i);
          show(e, <><div className="text-muted">{new Date(p.t).toLocaleDateString("pl-PL")}</div><div className="num">{money(p.v)} USD / transakcję</div></>);
        }}>
        <line x1="0" x2="100" y1={zero} y2={zero} stroke="var(--color-line)" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
        <path d={path} fill="none" stroke="var(--color-accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {hover != null && (
          <line x1={(hover / (points.length - 1)) * 100} x2={(hover / (points.length - 1)) * 100} y1="0" y2={height}
            stroke="var(--color-muted)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        )}
        {hover != null && <circle cx={(hover / (points.length - 1)) * 100} cy={ys[hover]} r="0" />}
      </svg>
      {layer}
    </div>
  );
}

/** Kalendarz dziennego wyniku (ostatnie `weeks` tygodni, pon–pt). Intensywność = wielkość wyniku; znak = kolor + podpowiedź. */
export function DailyCalendar({ days, weeks = 26 }: { days: { day: string; pnl: number; trades: number }[]; weeks?: number }) {
  const { box, show, hide, layer } = useTip();
  const byDay = new Map(days.map((d) => [d.day, d]));
  const last = days.length ? new Date(days[days.length - 1].day + "T00:00:00Z") : new Date();
  const end = new Date(Date.UTC(last.getUTCFullYear(), last.getUTCMonth(), last.getUTCDate()));
  const endMonday = new Date(end);
  endMonday.setUTCDate(end.getUTCDate() - ((end.getUTCDay() + 6) % 7));
  const start = new Date(endMonday);
  start.setUTCDate(endMonday.getUTCDate() - (weeks - 1) * 7);
  const vals = days.filter((d) => new Date(d.day) >= start).map((d) => Math.abs(d.pnl)).sort((a, b) => a - b);
  const ref = vals.length ? vals[Math.floor(vals.length * 0.9)] || vals[vals.length - 1] : 1;
  const level = (v: number) => (v === 0 ? 0 : Math.min(4, Math.ceil((Math.abs(v) / (ref || 1)) * 4)));
  const opacity = [0, 0.3, 0.5, 0.72, 0.95];
  const C = 14, LX = 22, TY = 14;                              // komórka, margines na dni tygodnia i miesiące
  const cells: ReactNode[] = [];
  const months: ReactNode[] = [];
  let lastMonth = -1;
  let lastLabelW = -9;
  for (let w = 0; w < weeks; w++) {
    const monday = new Date(start);
    monday.setUTCDate(start.getUTCDate() + w * 7);
    // etykieta miesiąca nad pierwszym tygodniem miesiąca — pomijamy, gdy za blisko poprzedniej
    if (monday.getUTCMonth() !== lastMonth) {
      lastMonth = monday.getUTCMonth();
      if (w - lastLabelW >= 3) {
        lastLabelW = w;
        months.push(<text key={`m${w}`} x={LX + w * C} y={10} fontSize="9" fill="var(--color-muted)">
          {monday.toLocaleDateString("pl-PL", { month: "short", timeZone: "UTC" })}</text>);
      }
    }
    for (let d = 0; d < 5; d++) {
      const dt = new Date(monday);
      dt.setUTCDate(monday.getUTCDate() + d);
      if (dt > end) continue;
      const key = dt.toISOString().slice(0, 10);
      const rec = byDay.get(key);
      const lv = rec ? level(rec.pnl) : 0;
      cells.push(
        <rect key={key} x={LX + w * C} y={TY + d * C} width={C - 2} height={C - 2} rx="2"
          fill={rec && lv ? (rec.pnl > 0 ? "var(--color-pos)" : "var(--color-neg)") : "var(--color-surface-2)"}
          opacity={rec && lv ? opacity[lv] : 1}
          onMouseMove={(e) => show(e, <><div className="text-muted">{dt.toLocaleDateString("pl-PL", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" })}</div><div className="num">{rec ? `${money(rec.pnl)} USD · ${rec.trades} tr.` : "bez transakcji"}</div></>)} />,
      );
    }
  }
  const W = LX + weeks * C, H = TY + 5 * C;
  return (
    <div ref={box} className="relative min-w-0" onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full max-w-[640px]" role="img" aria-label="Kalendarz dziennego wyniku">
        {months}
        {["Pn", "Śr", "Pt"].map((l, i) => (
          <text key={l} x={0} y={TY + i * 2 * C + 10} fontSize="9" fill="var(--color-muted)">{l}</text>
        ))}
        {cells}
      </svg>
      <div className="mt-1.5 flex items-center gap-1.5 text-[10px] text-muted">
        <span>strata</span>
        {[4, 2].map((l) => <span key={`n${l}`} className="h-2.5 w-2.5 rounded-sm bg-neg" style={{ opacity: opacity[l] }} />)}
        <span className="h-2.5 w-2.5 rounded-sm bg-surface-2" />
        {[2, 4].map((l) => <span key={`p${l}`} className="h-2.5 w-2.5 rounded-sm bg-pos" style={{ opacity: opacity[l] }} />)}
        <span>zysk</span>
      </div>
      {layer}
    </div>
  );
}
