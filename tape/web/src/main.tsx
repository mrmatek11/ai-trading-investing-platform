import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRootRoute, createRoute, createRouter, lazyRouteComponent, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Shell } from "./components/Shell";
import { Dashboard } from "./pages/Dashboard";
import { ImportPage } from "./pages/Import";
import { Trades } from "./pages/Trades";
import "./styles.css";

const root = createRootRoute({ component: Shell });
const routeTree = root.addChildren([
  createRoute({ getParentRoute: () => root, path: "/", component: Dashboard }),
  createRoute({ getParentRoute: () => root, path: "/trades", component: Trades }),
  // Globus (three.js) ładowany osobno — journal nie czeka na ~2 MB grafiki 3D.
  createRoute({ getParentRoute: () => root, path: "/globe", component: lazyRouteComponent(() => import("./pages/Globe"), "GlobePage") }),
  createRoute({ getParentRoute: () => root, path: "/import", component: ImportPage }),
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
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
