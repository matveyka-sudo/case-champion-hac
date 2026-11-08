import { useEffect } from "react";
export function useModalFocus(id: string, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const panel = document.getElementById(id);
    if (!panel) return;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    const elements = () =>
      Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]',
        ),
      ).filter((el) => el.getClientRects().length > 0);
    const first = elements()[0];
    first?.focus({ preventScroll: true });
    const key = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const all = elements(),
        first = all[0],
        last = all[all.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    panel.addEventListener("keydown", key);
    return () => {
      panel.removeEventListener("keydown", key);
      panel.removeAttribute("role");
      panel.removeAttribute("aria-modal");
      previous?.focus({ preventScroll: true });
    };
  }, [id, active]);
}
