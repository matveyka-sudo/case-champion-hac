import { useEffect, useSyncExternalStore } from "react";
const query = "(prefers-reduced-motion: reduce)";
const subscribe = (cb: () => void) => {
  const m = matchMedia(query);
  m.addEventListener("change", cb);
  return () => m.removeEventListener("change", cb);
};
export function useReducedMotion() {
  return useSyncExternalStore(
    subscribe,
    () => matchMedia(query).matches,
    () => false,
  );
}
export function useLandingMotion() {
  const reduced = useReducedMotion();
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) =>
        entries.forEach((en) => {
          if (en.isIntersecting) {
            en.target.classList.add("is-in");
            observer.unobserve(en.target);
          }
        }),
      { threshold: 0.18, rootMargin: "0px 0px -6% 0px" },
    );
    document
      .querySelectorAll("#landing [data-reveal]")
      .forEach((el) => observer.observe(el));
    let raf = 0;
    const tick = () => {
      raf = 0;
      const y = window.scrollY,
        vh = window.innerHeight;
      document
        .getElementById("lpHeader")
        ?.classList.toggle("is-solid", y > vh * 0.72);
      const hero = document.getElementById("top"),
        pv = document.getElementById("previewStage");
      if (!reduced) {
        hero?.style.setProperty(
          "--p",
          String(Math.max(0, Math.min(1, y / (vh * 0.95)))),
        );
        if (pv)
          pv.style.setProperty(
            "--q",
            String(
              Math.max(
                0,
                Math.min(
                  1,
                  (vh - pv.getBoundingClientRect().top) / (vh * 0.85),
                ),
              ),
            ),
          );
      } else {
        hero?.style.removeProperty("--p");
        pv?.style.removeProperty("--q");
      }
    };
    const scroll = () => {
      if (!raf) raf = requestAnimationFrame(tick);
    };
    window.addEventListener("scroll", scroll, { passive: true });
    tick();
    const ready = requestAnimationFrame(() =>
      document.body.classList.add("is-ready"),
    );
    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", scroll);
      cancelAnimationFrame(raf);
      cancelAnimationFrame(ready);
    };
  }, [reduced]);
}
