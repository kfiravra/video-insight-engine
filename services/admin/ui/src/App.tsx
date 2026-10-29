import { useMemo, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, useNavigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { hasApiKey, logout } from './lib/api';
import { LoginPrompt } from './components/LoginPrompt';
import { DashboardPage } from './pages/DashboardPage';
import { UsagePage } from './pages/UsagePage';
import { HealthPage } from './pages/HealthPage';
import { AlertsPage } from './pages/AlertsPage';
import { VideosPage } from './pages/VideosPage';
import { VideoDetailPage } from './pages/VideoDetailPage';
import { UsersPage } from './pages/UsersPage';
import { AuditPage } from './pages/AuditPage';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ErrorState } from './components/ErrorState';
import { RangePicker } from './components/RangePicker';
import { CommandPalette } from './components/CommandPalette';
import type { Command } from './components/CommandPalette';
import { useUrlRange } from './hooks/use-url-range';
import { useCommandPalette } from './hooks/use-command-palette';
import { GridIcon, VideoIcon, BarChartIcon, HeartPulseIcon, BellIcon, UsersIcon, ClockIcon } from './components/icons';
import './index.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 10_000 } },
});

const navItems = [
  { to: '/', label: 'Dashboard', icon: GridIcon },
  { to: '/videos', label: 'Videos', icon: VideoIcon },
  { to: '/users', label: 'Users', icon: UsersIcon },
  { to: '/audit-log', label: 'Audit', icon: ClockIcon },
  { to: '/usage', label: 'Usage', icon: BarChartIcon },
  { to: '/health', label: 'Health', icon: HeartPulseIcon },
  { to: '/alerts', label: 'Alerts', icon: BellIcon },
];

function LogOutIcon({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}

function LogoutButton() {
  return (
    <button
      type="button"
      onClick={() => logout()}
      aria-label="Sign out"
      title="Sign out"
      data-testid="logout-button"
      className="inline-flex items-center justify-center w-8 h-8 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-dim)] transition-colors"
    >
      <LogOutIcon size={14} />
    </button>
  );
}

function CommandHint() {
  return (
    <span
      aria-hidden="true"
      className="hidden md:inline-flex items-center gap-1 text-[10px] font-mono text-[var(--color-text-faint)] border border-[var(--color-border)] rounded px-1.5 py-0.5"
      data-testid="command-hint"
    >
      <span>⌘K</span>
    </span>
  );
}

interface LayoutProps {
  children: React.ReactNode;
  days: number;
  setDays: (days: number) => void;
}

function Layout({ children, days, setDays }: LayoutProps) {
  return (
    <div className="min-h-screen bg-[var(--color-surface)]">
      <header className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-surface)]/80 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4 h-12 flex items-center gap-4">
          <span className="font-bold text-sm tracking-tight">VIE Admin</span>
          <div className="w-px h-5 bg-[var(--color-border)]" />
          <nav className="flex gap-1 overflow-x-auto">
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  className={({ isActive }) =>
                    `flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                      isActive
                        ? 'bg-[var(--color-primary)] text-white'
                        : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-dim)]'
                    }`
                  }
                >
                  <Icon />
                  {item.label}
                </NavLink>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <CommandHint />
            <RangePicker value={days} onChange={setDays} />
            <LogoutButton />
          </div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
    </div>
  );
}

function errorFallback(error: Error, reset: () => void) {
  const handleRetry = () => {
    reset();
    window.location.reload();
  };
  return (
    <div className="min-h-screen flex items-center justify-center p-6 bg-[var(--color-surface)]">
      <div className="w-full max-w-md">
        <ErrorState
          error={error}
          onRetry={handleRetry}
          title="The admin console hit an unexpected error"
        />
      </div>
    </div>
  );
}

function AppShell() {
  const [days, setDays] = useUrlRange(30);
  const { open, setOpen } = useCommandPalette();
  const navigate = useNavigate();

  const commands = useMemo<Command[]>(
    () => [
      {
        id: 'nav-dashboard',
        label: 'Go to Dashboard',
        hint: 'g d',
        keywords: ['home', 'overview'],
        action: () => navigate('/'),
      },
      {
        id: 'nav-videos',
        label: 'Go to Videos',
        hint: 'g v',
        keywords: ['library'],
        action: () => navigate('/videos'),
      },
      {
        id: 'nav-users',
        label: 'Go to Users',
        hint: 'g s',
        keywords: ['users', 'people', 'cost', 'credit'],
        action: () => navigate('/users'),
      },
      {
        id: 'nav-audit',
        label: 'Go to Audit log',
        hint: 'g l',
        keywords: ['audit', 'history', 'changes', 'admin actions'],
        action: () => navigate('/audit-log'),
      },
      {
        id: 'nav-usage',
        label: 'Go to Usage',
        hint: 'g u',
        keywords: ['cost', 'tokens'],
        action: () => navigate('/usage'),
      },
      {
        id: 'nav-health',
        label: 'Go to Health',
        hint: 'g h',
        keywords: ['status', 'services'],
        action: () => navigate('/health'),
      },
      {
        id: 'nav-alerts',
        label: 'Go to Alerts',
        hint: 'g a',
        keywords: ['warnings'],
        action: () => navigate('/alerts'),
      },
      {
        id: 'range-7',
        label: 'Set range: last 7 days',
        keywords: ['range', 'date', 'week'],
        action: () => setDays(7),
      },
      {
        id: 'range-30',
        label: 'Set range: last 30 days',
        keywords: ['range', 'date', 'month'],
        action: () => setDays(30),
      },
      {
        id: 'range-90',
        label: 'Set range: last 90 days',
        keywords: ['range', 'date', 'quarter'],
        action: () => setDays(90),
      },
      {
        id: 'logout',
        label: 'Sign out',
        keywords: ['logout', 'exit', 'sign off'],
        action: () => logout(),
      },
    ],
    [navigate, setDays],
  );

  return (
    <Layout days={days} setDays={setDays}>
      <ErrorBoundary fallback={errorFallback}>
        <Routes>
          <Route path="/" element={<DashboardPage days={days} />} />
          <Route path="/videos" element={<VideosPage days={days} />} />
          <Route path="/videos/:videoId" element={<VideoDetailPage />} />
          <Route path="/users" element={<UsersPage days={days} />} />
          <Route path="/audit-log" element={<AuditPage />} />
          <Route path="/usage" element={<UsagePage days={days} />} />
          <Route path="/health" element={<HealthPage />} />
          <Route path="/alerts" element={<AlertsPage days={days} />} />
        </Routes>
      </ErrorBoundary>
      <CommandPalette open={open} onClose={() => setOpen(false)} commands={commands} />
    </Layout>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(hasApiKey());

  if (!authed) {
    return <LoginPrompt onLogin={() => setAuthed(true)} />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppShell />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
