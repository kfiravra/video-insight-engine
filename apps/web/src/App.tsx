import { useEffect, lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider } from "@/components/theme-provider";
import { ErrorBoundary } from "@/components/ui/error-boundary";
import { useAuthStore } from "@/stores/auth-store";
import { useWebSocket } from "@/hooks/use-websocket";
import { useProcessingManager } from "@/features/video-output/hooks/use-processing-manager";
import { Loader2, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

// Lazy load toast components - not needed for initial render
const Toaster = lazy(() =>
  import("@/components/ui/sonner").then((m) => ({ default: m.Toaster }))
);
const toastModule = () => import("sonner");

// Lazy load route components for code splitting
const LoginPage = lazy(() =>
  import("@/pages/LoginPage").then((m) => ({ default: m.LoginPage }))
);
const RegisterPage = lazy(() =>
  import("@/pages/RegisterPage").then((m) => ({ default: m.RegisterPage }))
);
const LandingPage = lazy(() =>
  import("@/pages/LandingPage").then((m) => ({ default: m.LandingPage }))
);
const BoardPage = lazy(() =>
  import("@/pages/BoardPage").then((m) => ({ default: m.BoardPage }))
);
const GeneratePage = lazy(() =>
  import("@/pages/GeneratePage").then((m) => ({ default: m.GeneratePage }))
);
const SharePage = lazy(() =>
  import("@/pages/SharePage").then((m) => ({ default: m.SharePage }))
);
const BillingPage = lazy(() =>
  import("@/pages/BillingPage").then((m) => ({ default: m.BillingPage }))
);
const VideoDetailPage = lazy(() =>
  import("@/pages/VideoDetailPage").then((m) => ({
    default: m.VideoDetailPage,
  }))
);

// Dev-only pages - completely tree-shaken in production
const DesignSystemPage = import.meta.env.DEV
  ? lazy(() =>
      import("@/pages/dev/DesignSystemPage").then((m) => ({
        default: m.DesignSystemPage,
      }))
    )
  : null;
const StreamingPreviewPage = import.meta.env.DEV
  ? lazy(() =>
      import("@/pages/dev/StreamingPreviewPage").then((m) => ({
        default: m.StreamingPreviewPage,
      }))
    )
  : null;

// Loading fallback for lazy-loaded routes with ARIA live region
function RouteLoadingFallback() {
  return (
    <div
      className="flex min-h-screen items-center justify-center"
      role="status"
      aria-live="polite"
    >
      <Loader2
        className="h-8 w-8 animate-spin text-primary"
        aria-hidden="true"
      />
      <span className="sr-only">Loading page...</span>
    </div>
  );
}

// Error fallback for chunk loading failures
function ChunkLoadError() {
  const handleReload = () => window.location.reload();

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-4 text-center">
      <AlertTriangle
        className="h-12 w-12 text-destructive"
        aria-hidden="true"
      />
      <h1 className="text-xl font-semibold">Failed to load page</h1>
      <p className="text-muted-foreground max-w-md">
        There was a problem loading this page. This might be due to a network
        issue or a new version being deployed.
      </p>
      <Button onClick={handleReload} className="mt-2">
        Reload Page
      </Button>
    </div>
  );
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isLoading = useAuthStore((s) => s.isLoading);

  if (isLoading) {
    return <RouteLoadingFallback />;
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function AppRoutes() {
  const checkAuth = useAuthStore((s) => s.checkAuth);
  const logoutReason = useAuthStore((s) => s.logoutReason);
  const clearLogoutReason = useAuthStore((s) => s.clearLogoutReason);

  // Connect to WebSocket for real-time updates
  useWebSocket();

  // Manage processing streams for all videos (auto-resume, sidebar sync)
  useProcessingManager();

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // Show toast when session expires and user is redirected to login
  useEffect(() => {
    if (logoutReason) {
      toastModule().then(({ toast }) => {
        toast.error(logoutReason);
      });
      clearLogoutReason();
    }
  }, [logoutReason, clearLogoutReason]);

  return (
    <ErrorBoundary fallback={<ChunkLoadError />}>
      <Suspense fallback={<RouteLoadingFallback />}>
        <Routes>
          {/* Public routes */}
          <Route path="/" element={<LandingPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/s/:slug" element={<SharePage />} />
          {/* Legacy path — older share links used /share/:slug. Mount the
              same page so expired or wrong-path links surface the coded
              not-found state instead of silently redirecting to landing. */}
          <Route path="/share/:slug" element={<SharePage />} />

          {/* Protected routes */}
          <Route
            path="/board"
            element={
              <ProtectedRoute>
                <BoardPage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/generate"
            element={
              <ProtectedRoute>
                <GeneratePage />
              </ProtectedRoute>
            }
          />
          <Route
            path="/video/:id"
            element={
              <ProtectedRoute>
                <VideoDetailPage />
              </ProtectedRoute>
            }
          />

          <Route
            path="/settings/billing"
            element={
              <ProtectedRoute>
                <BillingPage />
              </ProtectedRoute>
            }
          />

          {/* Backward compat redirect */}
          <Route path="/dashboard" element={<Navigate to="/board" replace />} />

          {/* Dev-only routes - completely tree-shaken in production */}
          {import.meta.env.DEV && DesignSystemPage && (
            <Route
              path="/dev/design-system"
              element={<DesignSystemPage />}
            />
          )}
          {import.meta.env.DEV && StreamingPreviewPage && (
            <Route
              path="/dev/streaming-preview"
              element={<StreamingPreviewPage />}
            />
          )}
          {/* Catch-all */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}

export function App() {
  return (
    <ThemeProvider defaultTheme="dark" storageKey="vie-theme">
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
      <Suspense fallback={null}>
        <Toaster />
      </Suspense>
    </ThemeProvider>
  );
}
