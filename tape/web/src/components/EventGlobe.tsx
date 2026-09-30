import Globe, { type GlobeInstance } from "globe.gl";
import { polygonToCells } from "h3-js";
import { useEffect, useRef, useState } from "react";
import type { MeshPhongMaterial } from "three";
import { feature } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import countries from "world-atlas/countries-110m.json";
import type { MarketEvent } from "../api";

type Props = {
  events: MarketEvent[];
  asset: "XAU" | "XAG";
  selectedId: string | null;
  onSelect: (id: string) => void;
};

const HEX_RESOLUTION = 3;

// Niektóre poligony (np. Korea Płn. w world-atlas 110m) wywracają H3 przy tej rozdzielczości,
// a błąd w globe.gl przerywa rysowanie warstwy — odfiltrowujemy je z góry.
function hexable(f: GeoJSON.Feature): boolean {
  const g = f.geometry;
  const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
  try {
    polys.forEach((p) => polygonToCells(p, HEX_RESOLUTION, true));
    return true;
  } catch {
    return false;
  }
}

const LAND = (() => {
  const topo = countries as unknown as Topology<{ countries: GeometryCollection }>;
  const fc = feature(topo, topo.objects.countries);
  return ("features" in fc ? fc.features : [fc]).filter(hexable);
})();

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** Zmiana palety (tryb dla daltonistów) przerysowuje kolory punktów. */
function usePalette(): string {
  const [palette, setPalette] = useState(document.documentElement.dataset.palette ?? "standard");
  useEffect(() => {
    const mo = new MutationObserver(() => setPalette(document.documentElement.dataset.palette ?? "standard"));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-palette"] });
    return () => mo.disconnect();
  }, []);
  return palette;
}

export function EventGlobe({ events, asset, selectedId, onSelect }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const globe = useRef<GlobeInstance | null>(null);
  const select = useRef(onSelect);
  select.current = onSelect;
  const palette = usePalette();

  // Scena tworzona raz; dane aktualizowane w osobnym efekcie.
  useEffect(() => {
    const el = box.current!;
    const g = new Globe(el, { rendererConfig: { antialias: true, alpha: true } })
      .backgroundColor("rgba(0,0,0,0)")
      .showAtmosphere(false)
      .showGraticules(true)
      .hexPolygonsData(LAND)
      .hexPolygonResolution(HEX_RESOLUTION)
      .hexPolygonMargin(0.35)
      .hexPolygonUseDots(true)
      .hexPolygonColor(() => "#4a4a52")
      .pointLat("lat")
      .pointLng("lon")
      .pointAltitude(0.015)
      .pointResolution(16)
      .ringLat("lat")
      .ringLng("lon")
      .ringMaxRadius(3.2)
      .ringPropagationSpeed(1.4)
      .ringRepeatPeriod(1600)
      .onPointClick((p) => select.current((p as MarketEvent).id))
      .pointOfView({ lat: 28, lng: 15, altitude: 2.1 });
    const material = g.globeMaterial() as MeshPhongMaterial;
    material.color.set("#101013");
    material.shininess = 0; // matowa kula — bez odblasku, który odciąga wzrok od punktów
    const controls = g.controls();
    controls.autoRotate = false;
    controls.minDistance = 160;
    controls.maxDistance = 600;
    const resize = () => g.width(el.clientWidth).height(el.clientHeight);
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    globe.current = g;
    return () => {
      ro.disconnect();
      g._destructor();
      el.innerHTML = "";
      globe.current = null;
    };
  }, []);

  // Obróć kulę do zdarzenia wybranego z listy (mogło być po niewidocznej stronie).
  useEffect(() => {
    const g = globe.current;
    const e = events.find((x) => x.id === selectedId);
    if (!g || !e) return;
    g.pointOfView({ lat: e.lat, lng: e.lon, altitude: g.pointOfView().altitude }, 900);
  }, [selectedId]); // tylko przy zmianie wyboru — nie przy odświeżeniu danych

  useEffect(() => {
    const g = globe.current;
    if (!g) return;
    const pos = cssVar("--color-pos", "#2fbf71");
    const neg = cssVar("--color-neg", "#f2555a");
    const muted = cssVar("--color-muted", "#8a8a93");
    const color = (e: MarketEvent) => {
      const d = e.impacts[asset]?.direction ?? 0;
      return d > 0 ? pos : d < 0 ? neg : muted;
    };
    g.pointsData(events)
      .pointColor((o) => (o as MarketEvent).id === selectedId ? "#ededef" : color(o as MarketEvent))
      .pointRadius((o) => 0.35 + ((o as MarketEvent).impacts[asset]?.magnitude ?? 1) * 0.14)
      .pointLabel((o) => {
        const e = o as MarketEvent;
        return `<div style="font:12px 'IBM Plex Sans',sans-serif;background:#111113;border:1px solid #26262a;border-radius:6px;padding:6px 8px;color:#ededef">${escapeHtml(e.title)}<div style="color:#8a8a93">${escapeHtml(e.place)}</div></div>`;
      })
      .ringsData(events.filter((e) => e.age_minutes >= 0 && e.age_minutes < 60))
      .ringColor((o: object) => {
        const c = color(o as MarketEvent);
        return (t: number) => `${c}${Math.round((1 - t) * 160).toString(16).padStart(2, "0")}`;
      });
  }, [events, asset, selectedId, palette]);

  return <div ref={box} className="h-full w-full" aria-label="Globus zdarzeń" role="img" />;
}
