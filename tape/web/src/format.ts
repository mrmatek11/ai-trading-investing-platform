// Formatowanie liczb w stylu polskim: spacja tysięcy, przecinek dziesiętny, prawdziwy minus.

const nf = (digits: number) =>
  new Intl.NumberFormat("pl-PL", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export function money(v: number, digits = 2, sign = true): string {
  const s = nf(digits).format(Math.abs(v));
  if (!sign) return v < 0 ? `−${s}` : s;
  return v > 0 ? `+${s}` : v < 0 ? `−${s}` : s;
}

export function pct(v: number | null, digits = 1): string {
  return v == null ? "—" : `${nf(digits).format(v * 100)}%`;
}

export function num(v: number | null, digits = 2, sign = false): string {
  if (v == null) return "—";
  return sign ? money(v, digits) : money(v, digits, false);
}

export function r(v: number | null): string {
  return v == null ? "—" : `${money(v, 2)}R`;
}

export function tone(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-fg";
  return v > 0 ? "text-pos" : "text-neg";
}

export function when(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function age(minutes: number): string {
  if (minutes < 0) return "wkrótce";
  if (minutes < 60) return `${minutes} min`;
  return `${Math.round(minutes / 60)} h`;
}
