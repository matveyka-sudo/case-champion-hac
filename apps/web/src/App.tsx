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
import { AtlasProvider } from "./features/AtlasProvider";
import { atlasLocation, subscribeAtlasLocation } from "./features/analysis/savedPointer";
const requestedDataMode = new URLSearchParams(window.location.search).get("data") || import.meta.env.VITE_DATA_MODE || "demo";
const dataMode = requestedDataMode === "training" ? "api" : requestedDataMode;
const AtlasPage = lazy(() => dataMode === "api"
  ? import("./pages/ApiAtlasPage").then(m => ({default:m.ApiAtlasPage}))
  : import("./pages/AtlasPage").then(m => ({default:m.AtlasPage})),
);
const AddressAccessPage = lazy(() => import("./pages/AddressAccessPage"));
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
      subscribeAtlasLocation,
      atlasLocation,
      () => "",
    ),
    atlas = hash.startsWith("#atlas"),
    addressAccess = hash === "#address-access",
    savedScroll = useRef(0);
  useEffect(() => {
    document.title = addressAccess ? "Waydex — инфраструктура рядом с домом" : atlas ? "Waydex — атлас" : "Waydex — связи территорий";
  }, [atlas, addressAccess]);
  useEffect(() => {
    if (!atlas && !addressAccess) {
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
    }
  }, [hash, atlas, addressAccess]);
  useEffect(() => {
    if (atlas || addressAccess) {
      savedScroll.current = window.scrollY;
      window.scrollTo(0, 0);
      return () => window.scrollTo(0, savedScroll.current);
    }
  }, [atlas, addressAccess]);
  return (
    <Boundary>
      <AtlasProvider>
        <a
          className="visually-hidden-focusable"
          href={addressAccess ? "#address-access" : atlas ? "#atlas" : "#main"}
          onClick={(e) => {
            e.preventDefault();
            const el = document.getElementById(addressAccess ? "address-access" : atlas ? "atlas" : "main");
            el?.setAttribute("tabindex", "-1");
            el?.focus();
          }}
        >
          Перейти к содержанию
        </a>
        {addressAccess ? <Suspense fallback={<p role="status">Открываем поиск адреса…</p>}><AddressAccessPage /></Suspense> : atlas ? (
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
