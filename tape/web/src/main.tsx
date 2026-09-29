import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRootRoute, createRoute, createRouter, lazyRouteComponent, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AuthGate } from "./auth";
import { BookProvider } from "./book";
import { Shell } from "./components/Shell";
import { BriefPage } from "./pages/Brief";
import { ConnectionsPage } from "./pages/Connections";
import { Dashboard } from "./pages/Dashboard";
import { ImportPage } from "./pages/Import";
import { Playbooks } from "./pages/Playbooks";
import { PortfolioPage } from "./pages/Portfolio";
import { RiskPage } from "./pages/Risk";
import { SettingsPage } from "./pages/Settings";
import { Trades } from "./pages/Trades";
import "./styles.css";

const root = createRootRoute({ component: Shell });
const routeTree = root.addChildren([
  createRoute({ getParentRoute: () => root, path: "/", component: Dashboard }),
  createRoute({ getParentRoute: () => root, path: "/brief", component: BriefPage }),
  createRoute({ getParentRoute: () => root, path: "/trades", component: Trades }),
  // Wykres transakcji (lightweight-charts) też osobno — lista i dashboard ładują się szybciej.
  createRoute({ getParentRoute: () => root, path: "/trades/$key", component: lazyRouteComponent(() => import("./pages/TradeDetail"), "TradeDetail") }),
  createRoute({ getParentRoute: () => root, path: "/playbooks", component: Playbooks }),
  createRoute({ getParentRoute: () => root, path: "/portfolio", component: PortfolioPage }),
  // Globus (three.js) ładowany osobno — journal nie czeka na ~2 MB grafiki 3D.
  createRoute({ getParentRoute: () => root, path: "/globe", component: lazyRouteComponent(() => import("./pages/Globe"), "GlobePage") }),
  createRoute({ getParentRoute: () => root, path: "/import", component: ImportPage }),
  createRoute({ getParentRoute: () => root, path: "/connections", component: ConnectionsPage }),
  createRoute({ getParentRoute: () => root, path: "/risk", component: RiskPage }),
  createRoute({ getParentRoute: () => root, path: "/settings", component: SettingsPage }),
]);

const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        <BookProvider>
          <RouterProvider router={router} />
        </BookProvider>
      </AuthGate>
    </QueryClientProvider>
  </StrictMode>,
);
