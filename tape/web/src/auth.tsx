import { createContext, lazy, Suspense, useContext, type ReactNode } from "react";

/** Klucz publiczny Clerk; bez niego aplikacja działa w trybie jednego użytkownika (bez logowania). */
export const CLERK_KEY: string | undefined = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || undefined;

export const AccountMenu = createContext<ReactNode>(null);
export const useAccountMenu = () => useContext(AccountMenu);

// Clerk ładowany tylko, gdy logowanie jest włączone — tryb jednego użytkownika go nie pobiera.
const ClerkGate = lazy(() => import("./ClerkGate"));

export function AuthGate({ children }: { children: ReactNode }) {
  if (!CLERK_KEY) return <>{children}</>;
  return (
    <Suspense fallback={<div className="min-h-screen bg-bg" />}>
      <ClerkGate publishableKey={CLERK_KEY}>{children}</ClerkGate>
    </Suspense>
  );
}
