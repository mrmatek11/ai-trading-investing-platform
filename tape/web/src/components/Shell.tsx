import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useAccountMenu } from "../auth";

const NAV: { to: string; label: string; icon: ReactNode }[] = [
  {
    to: "/",
    label: "Journal",
    icon: (
      <>
        <rect x="5" y="3" width="14" height="18" rx="2" />
        <path d="M9 8h6M9 12h6M9 16h3" />
      </>
    ),
  },
  {
    to: "/trades",
    label: "Transakcje",
    icon: <path d="M4 7h16M4 12h16M4 17h10" />,
  },
  {
    to: "/playbooks",
    label: "Playbooki",
    icon: (
      <>
        <path d="M4 5h11l5 5v9H4z" />
        <path d="M8 11l2 2 4-4" />
      </>
    ),
  },
  {
    to: "/portfolio",
    label: "Portfel",
    icon: (
      <>
        <path d="M12 3a9 9 0 1 0 9 9h-9z" />
        <path d="M15 3.5A9 9 0 0 1 20.5 9H15z" />
      </>
    ),
  },
  {
    to: "/globe",
    label: "Globus",
    icon: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18" />
      </>
    ),
  },
  {
    to: "/risk",
    label: "Ryzyko",
    icon: <path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z" />,
  },
  {
    to: "/import",
    label: "Import",
    icon: <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />,
  },
  {
    to: "/connections",
    label: "Połączenia",
    icon: (
      <>
        <path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3" />
        <path d="M18 3v4h-4M6 21v-4h4" />
      </>
    ),
  },
];

// Polecenia w stylu terminala: „GLOBE XAU”, „JRNL”, „IMPORT”, „TRADES”, „RISK”.
const COMMANDS: Record<string, string> = {
  GLOBE: "/globe",
  JRNL: "/",
  JOURNAL: "/",
  TRADES: "/trades",
  IMPORT: "/import",
  RISK: "/risk",
  PLAYBOOK: "/playbooks",
  PLAYBOOKS: "/playbooks",
  SETUPS: "/playbooks",
  CALC: "/risk",
  PROP: "/risk",
  SYNC: "/connections",
  PORT: "/portfolio",
  PORTFOLIO: "/portfolio",
  PF: "/portfolio",
  CONNECT: "/connections",
};

export function Shell() {
  const navigate = useNavigate();
  const accountMenu = useAccountMenu();
  const input = useRef<HTMLInputElement>(null);
  const [cmd, setCmd] = useState("");
  const [error, setError] = useState("");
  const [palette, setPalette] = useState(() => {
    try {
      return localStorage.getItem("tape-palette") ?? "standard";
    } catch {
      return "standard";
    }
  });

  useEffect(() => {
    document.documentElement.dataset.palette = palette;
    try {
      localStorage.setItem("tape-palette", palette);
    } catch {
      /* brak dostępu do storage — ustawienie tylko na tę sesję */
    }
  }, [palette]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement;
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const run = () => {
    const words = cmd.trim().toUpperCase().split(/\s+/);
    const target = words.map((w) => COMMANDS[w]).find(Boolean);
    if (target) {
      setError("");
      setCmd("");
      navigate({ to: target });
    } else if (cmd.trim()) {
      setError("Nieznane polecenie. Spróbuj: GLOBE, JRNL, PORT, TRADES, PLAYBOOK, RISK, IMPORT, SYNC");
    }
  };

  return (
    <div className="flex h-full min-h-screen bg-bg text-fg">
      <nav aria-label="Moduły" className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-line py-3">
        <div className="num mb-4 flex h-7 w-7 items-center justify-center rounded-md border-[1.5px] border-fg text-[13px] font-medium">
          T
        </div>
        {NAV.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            aria-label={item.label}
            title={item.label}
            className="flex h-11 w-11 items-center justify-center rounded-md text-muted hover:text-fg"
            activeProps={{ className: "bg-surface-2 !text-fg" }}
            activeOptions={{ exact: item.to === "/" }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              {item.icon}
            </svg>
          </Link>
        ))}
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setPalette(palette === "standard" ? "colorblind" : "standard")}
          aria-label="Przełącz paletę dla daltonistów"
          title={palette === "standard" ? "Paleta dla daltonistów" : "Paleta standardowa"}
          className="flex h-11 w-11 items-center justify-center rounded-md text-muted hover:text-fg"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="8" />
            <path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" />
          </svg>
        </button>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-5">
          <form
            className="flex h-8 w-full max-w-xl items-center gap-2 rounded-md border border-line bg-surface px-2.5 text-muted"
            onSubmit={(e) => {
              e.preventDefault();
              run();
            }}
          >
            <span aria-hidden className="text-accent">›</span>
            <label className="sr-only" htmlFor="command">
              Polecenie
            </label>
            <input
              id="command"
              ref={input}
              value={cmd}
              onChange={(e) => {
                setCmd(e.target.value);
                setError("");
              }}
              placeholder="Polecenie: GLOBE, PORT, JRNL, TRADES, RISK, SYNC…"
              className="num flex-1 bg-transparent text-[13px] text-fg outline-none placeholder:text-muted"
              autoComplete="off"
            />
            <kbd className="num rounded border border-line px-1.5 text-[11px]">⌘K</kbd>
          </form>
          {error && <span role="status" className="text-xs text-warn">{error}</span>}
          <div className="flex-1" />
          {accountMenu}
        </header>
        <main className="min-h-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
