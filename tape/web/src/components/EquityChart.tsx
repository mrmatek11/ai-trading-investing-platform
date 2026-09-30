import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

type Point = { t: string; equity: number };

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Krzywa kapitału: jedna linia, bez wypełnienia, oś Y po prawej (PRODUCT_SPEC 5.5).
export function EquityChart({ points, height = 180 }: { points: Point[]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = box.current;
    if (!el || points.length < 2) return;
    const xs = points.map((p) => new Date(p.t).getTime() / 1000);
    const ys = points.map((p) => p.equity);
    const fg = cssVar("--color-fg") || "#ededef";
    const muted = cssVar("--color-muted") || "#8a8a93";
    const grid = cssVar("--color-line-soft") || "#1a1a1d";
    const plot = new uPlot(
      {
        width: el.clientWidth,
        height,
        legend: { show: false },
        cursor: { y: false },
        scales: { x: { time: true } },
        axes: [
          { stroke: muted, grid: { show: false }, ticks: { show: false }, font: "11px IBM Plex Mono" },
          { side: 1, stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { show: false }, font: "11px IBM Plex Mono", size: 64 },
        ],
        series: [{}, { stroke: fg, width: 1.5, points: { show: false } }],
      },
      [xs, ys],
      el,
    );
    const onResize = () => plot.setSize({ width: el.clientWidth, height });
    const ro = new ResizeObserver(onResize);
    ro.observe(el);
    return () => {
      ro.disconnect();
      plot.destroy();
    };
  }, [points, height]);

  if (points.length < 2) {
    return <div className="flex items-center justify-center text-muted" style={{ height }}>Za mało zamkniętych transakcji na wykres.</div>;
  }
  return <div ref={box} className="w-full" aria-label="Krzywa kapitału" role="img" />;
}
