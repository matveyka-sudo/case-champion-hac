import {
  Component,
  lazy,
  Suspense,
  useEffect,
  useRef,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { LandingPage } from "./pages/LandingPage";
const dataMode = new URLSearchParams(window.location.search).get("data") || import.meta.env.VITE_DATA_MODE || "demo";
const AtlasPage = lazy(() => dataMode === "api"
  ? import("./pages/ApiAtlasPage").then(m => ({default:m.ApiAtlasPage}))
  : import("./pages/AtlasPage").then(m => ({default:m.AtlasPage})),
);
import { AtlasProvider } from "./features/AtlasProvider";
const subscribe = (cb: () => void) => {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
};
class Boundary extends Component<{ children: ReactNode }, { error: boolean }> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  render() {
    return this.state.error ? (
      <main className="wrap" style={{ padding: 40 }} role="alert">
        <h1>Не удалось открыть Waydex</h1>
        <p>Обновите страницу и попробуйте ещё раз.</p>
        <button onClick={() => window.location.reload()}>Обновить</button>
      </main>
    ) : (
      this.props.children
    );
  }
}
export default function App() {
  const hash = useSyncExternalStore(
      subscribe,
      () => location.hash,
      () => "",
    ),
    atlas = hash.startsWith("#atlas"),
    savedScroll = useRef(0);
  useEffect(() => {
    document.title = atlas ? "Waydex — атлас" : "Waydex — связи территорий";
  }, [atlas]);
  useEffect(() => {
    if (!atlas) {
      const el = document.getElementById(hash.slice(1));
      if (el) {
        const raf = requestAnimationFrame(() =>
          el.scrollIntoView({
            behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
              ? "instant"
              : "smooth",
          }),
        );
        return () => cancelAnimationFrame(raf);
      }
    } else {
      savedScroll.current = window.scrollY;
      window.scrollTo(0, 0);
      return () => window.scrollTo(0, savedScroll.current);
    }
  }, [hash, atlas]);
  return (
    <Boundary>
      <AtlasProvider>
        <a
          className="visually-hidden-focusable"
          href={atlas ? "#atlas" : "#main"}
          onClick={(e) => {
            e.preventDefault();
            const el = document.getElementById(atlas ? "atlas" : "main");
            el?.setAttribute("tabindex", "-1");
            el?.focus();
          }}
        >
          Перейти к содержанию
        </a>
        {atlas ? (
          <Suspense
            fallback={
              <div className="wrap" role="status" style={{ padding: 40 }}>
                Открываем атлас…
              </div>
            }
          >
            <AtlasPage />
          </Suspense>
        ) : (
          <LandingPage dataMode={dataMode === "api" ? "api" : "demo"} />
        )}
      </AtlasProvider>
    </Boundary>
  );
}
