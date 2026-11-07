import { Component, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AtlasPage } from '../pages/AtlasPage';
import { HomePage } from '../pages/HomePage';
import { useRoute } from './router';
import { RouteLink } from '../shared/ui/RouteLink';
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 60_000, retry: false, refetchOnWindowFocus: false },
  },
});
class ErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="container py-5" role="alert">
        <h1>Не удалось открыть приложение</h1>
        <p>Обновите страницу и попробуйте ещё раз.</p>
      </main>
    ) : (
      this.props.children
    );
  }
}
function Routes() {
  const route = useRoute();
  return (
    <>
      <button
        type="button"
        className="visually-hidden-focusable btn btn-link"
        onClick={() => {
          document.getElementById('main')?.focus();
        }}
      >
        К содержимому
      </button>
      <header className="border-bottom">
        <nav
          className="container d-flex align-items-center gap-4 py-3"
          aria-label="Основная навигация"
        >
          <RouteLink className="fw-bold text-dark" to="/">
            Waydex
          </RouteLink>
          <RouteLink
            to="/atlas"
            aria-current={route === '/atlas' ? 'page' : undefined}
          >
            Атлас
          </RouteLink>
        </nav>
      </header>
      <main id="main" tabIndex={-1} className="container py-4">
        {route === '/' ? (
          <HomePage />
        ) : route === '/atlas' ? (
          <AtlasPage />
        ) : (
          <>
            <h1>Страница не найдена</h1>
            <RouteLink to="/">На главную</RouteLink>
          </>
        )}
      </main>
    </>
  );
}
export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <Routes />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
