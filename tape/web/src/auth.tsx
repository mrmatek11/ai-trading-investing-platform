import { useQuery } from "@tanstack/react-query";
import { createContext, lazy, Suspense, useContext, type ReactNode } from "react";

/** Klucz publiczny Clerk; bez niego logowanie zależy od serwera (Discord) albo tryb jednego użytkownika. */
export const CLERK_KEY: string | undefined = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || undefined;

export const AccountMenu = createContext<ReactNode>(null);
export const useAccountMenu = () => useContext(AccountMenu);

// Clerk ładowany tylko, gdy logowanie Clerk jest włączone.
const ClerkGate = lazy(() => import("./ClerkGate"));

type AuthConfig = { clerk: boolean; discord: boolean; single_user: boolean };
type Me = { account: string; name: string | null; avatar: string | null; provider: string };

const LOGIN_ERRORS: Record<string, string> = {
  cancelled: "Logowanie anulowane w Discordzie.",
  state: "Sesja logowania wygasła albo została przerwana — spróbuj jeszcze raz.",
  discord: "Discord nie potwierdził logowania — spróbuj jeszcze raz.",
};

function Logo() {
  return <div className="num flex h-10 w-10 items-center justify-center rounded-md border-[1.5px] border-fg text-lg font-medium">T</div>;
}

function DiscordLogin() {
  const err = new URLSearchParams(window.location.search).get("login_error");
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-bg p-6 text-center">
      <Logo />
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Tape</h1>
        <p className="mt-1 text-muted">Journal, portfel i terminal dla traderów złota i srebra.</p>
      </div>
      {err && <p role="alert" className="text-warn">{LOGIN_ERRORS[err] ?? "Logowanie nie powiodło się."}</p>}
      <a href="/api/auth/discord/login" className="flex h-11 items-center gap-2.5 rounded-md bg-[#5865F2] px-5 font-medium text-white hover:bg-[#4752c4]">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
          <path d="M20.3 4.4A19.8 19.8 0 0 0 15.4 3l-.6 1.3a18.4 18.4 0 0 0-5.6 0L8.6 3a19.7 19.7 0 0 0-4.9 1.5C.6 9.1-.3 13.6.1 18.1a19.9 19.9 0 0 0 6 3l1.3-2.1a12.9 12.9 0 0 1-2-1l.5-.4a14.2 14.2 0 0 0 12.2 0l.5.4c-.6.4-1.3.7-2 1l1.3 2.1a19.8 19.8 0 0 0 6-3c.5-5.2-.9-9.7-3.6-13.7ZM8 15.3c-1.2 0-2.2-1.1-2.2-2.4S6.8 10.5 8 10.5s2.2 1.1 2.2 2.4-1 2.4-2.2 2.4Zm8 0c-1.2 0-2.2-1.1-2.2-2.4s1-2.4 2.2-2.4 2.2 1.1 2.2 2.4-1 2.4-2.2 2.4Z" />
        </svg>
        Zaloguj przez Discord
      </a>
      <p className="max-w-sm text-xs text-muted">Pobieramy tylko identyfikator, nazwę i awatar (zakres „identify”). Nie widzimy Twoich serwerów ani wiadomości.</p>
    </div>
  );
}

function DiscordAccount({ me }: { me: Me }) {
  return (
    <div className="flex items-center gap-2 text-[13px]">
      {me.avatar ? <img src={me.avatar} alt="" className="h-7 w-7 rounded-full" /> : <span className="h-7 w-7 rounded-full bg-surface-2" />}
      <span className="hidden max-w-32 truncate sm:inline">{me.name}</span>
      <button
        type="button"
        className="text-xs text-muted hover:text-fg"
        onClick={async () => {
          await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
          window.location.assign("/");
        }}
      >
        Wyloguj
      </button>
    </div>
  );
}

function DiscordGate({ children }: { children: ReactNode }) {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const r = await fetch("/api/auth/me");
      if (r.status === 401) return null;
      if (!r.ok) throw new Error(`${r.status}`);
      return (await r.json()) as Me;
    },
    retry: false,
    staleTime: 5 * 60_000,
  });
  if (me.isLoading) return <div className="min-h-screen bg-bg" />;
  if (!me.data) return <DiscordLogin />;
  return <AccountMenu.Provider value={<DiscordAccount me={me.data} />}>{children}</AccountMenu.Provider>;
}

export function AuthGate({ children }: { children: ReactNode }) {
  const cfg = useQuery({
    queryKey: ["auth-config"],
    queryFn: async () => (await (await fetch("/api/auth/config")).json()) as AuthConfig,
    enabled: !CLERK_KEY,
    staleTime: Infinity,
    retry: 1,
  });
  if (CLERK_KEY) {
    return (
      <Suspense fallback={<div className="min-h-screen bg-bg" />}>
        <ClerkGate publishableKey={CLERK_KEY}>{children}</ClerkGate>
      </Suspense>
    );
  }
  if (cfg.isLoading) return <div className="min-h-screen bg-bg" />;
  if (cfg.data?.discord) return <DiscordGate>{children}</DiscordGate>;
  return <>{children}</>;
}
