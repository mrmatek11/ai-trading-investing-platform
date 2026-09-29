import { ClerkProvider, Show, SignIn, useAuth, UserButton } from "@clerk/react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { setTokenProvider } from "./api";
import { AccountMenu } from "./auth";

function TokenBridge({ children }: { children: ReactNode }) {
  const { getToken, userId } = useAuth();
  const qc = useQueryClient();
  useEffect(() => {
    setTokenProvider(() => getToken());
    return () => setTokenProvider(null);
  }, [getToken]);
  useEffect(() => {
    qc.clear(); // zmiana użytkownika — nie pokazujemy danych poprzedniej sesji
  }, [userId, qc]);
  return <>{children}</>;
}

function SignInScreen() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6 bg-bg p-6">
      <div className="num flex h-10 w-10 items-center justify-center rounded-md border-[1.5px] border-fg text-lg font-medium">T</div>
      <SignIn routing="hash" />
    </div>
  );
}

export default function ClerkGate({ publishableKey, children }: { publishableKey: string; children: ReactNode }) {
  return (
    <ClerkProvider publishableKey={publishableKey}>
      <Show when="signed-in" fallback={<SignInScreen />}>
        <TokenBridge>
          <AccountMenu.Provider value={<UserButton />}>{children}</AccountMenu.Provider>
        </TokenBridge>
      </Show>
    </ClerkProvider>
  );
}
