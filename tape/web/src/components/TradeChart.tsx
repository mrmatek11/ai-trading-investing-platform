import { createChart, createSeriesMarkers, LineSeries, LineStyle, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { PositionDetail } from "../api";

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

const ts = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

/** Cena wokół transakcji z wejściami/wyjściami i liniami wejścia, wyjścia i SL (PRODUCT_SPEC 5.5). */
export function TradeChart({ detail, height = 360 }: { detail: PositionDetail; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const { position: p, fills, prices } = detail;

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const fg = cssVar("--color-fg", "#ededef");
    const muted = cssVar("--color-muted", "#8a8a93");
    const grid = cssVar("--color-line-soft", "#1a1a1d");
    const pos = cssVar("--color-pos", "#2fbf71");
    const neg = cssVar("--color-neg", "#f2555a");

    const chart = createChart(el, {
      height,
      width: el.clientWidth,
      layout: { background: { color: "transparent" }, textColor: muted, fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: grid } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
      crosshair: { vertLine: { labelBackgroundColor: "#18181b" }, horzLine: { labelBackgroundColor: "#18181b" } },
    });
    const series = chart.addSeries(LineSeries, { color: fg, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

    // Bez cen w bazie pokazujemy chociaż ścieżkę fill-i — lepsze niż pusty wykres.
    const points = prices.length
      ? prices.map((x) => ({ time: ts(x.t), value: x.p }))
      : fills.map((f) => ({ time: ts(f.ts), value: Number(f.price) }));
    const dedup = new Map<number, number>();
    points.forEach((pt) => dedup.set(pt.time, pt.value));
    series.setData([...dedup.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time: time as UTCTimestamp, value })));

    const entryDir = p.direction === "long" ? "buy" : "sell";
    createSeriesMarkers(
      series,
      fills
        .map((f) => ({
          time: ts(f.ts),
          position: f.side === "buy" ? ("belowBar" as const) : ("aboveBar" as const),
          shape: f.side === "buy" ? ("arrowUp" as const) : ("arrowDown" as const),
          color: f.side === entryDir ? fg : muted,
          text: `${f.side === entryDir ? "IN" : "OUT"} ${f.price}`,
        }))
        .sort((a, b) => a.time - b.time),
    );
    series.createPriceLine({ price: Number(p.avg_entry), color: fg, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: "IN" });
    if (p.avg_exit) series.createPriceLine({ price: Number(p.avg_exit), color: p.net_pnl >= 0 ? pos : neg, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "OUT" });
    if (p.initial_stop) series.createPriceLine({ price: Number(p.initial_stop), color: neg, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "SL" });
    chart.timeScale().fitContent();

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
    };
  }, [detail, height, p, fills, prices]);

  return <div ref={box} className="w-full" role="img" aria-label={`Wykres ${p.symbol} z wejściem i wyjściem`} />;
}
