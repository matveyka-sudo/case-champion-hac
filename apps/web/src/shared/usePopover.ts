import { useLayoutEffect, useRef, useState } from "react";
export function usePopover() {
  const [open, setOpen] = useState(false),
    trigger = useRef<HTMLButtonElement>(null),
    popup = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = popup.current,
      button = trigger.current;
    if (!node || !button) return;
    if (!open) {
      if (node.matches(":popover-open")) node.hidePopover();
      return;
    }
    const position = () => {
      const rect = button.getBoundingClientRect();
      const width = Math.min(Math.max(rect.width, 260), innerWidth - 24);
      const height = Math.min(340, innerHeight - 24);
      const below = innerHeight - rect.bottom - 10;
      const top =
        below >= Math.min(node.scrollHeight, height)
          ? rect.bottom + 8
          : Math.max(12, rect.top - Math.min(node.scrollHeight, height) - 8);
      Object.assign(node.style, {
        position: "fixed",
        inset: "auto",
        left: `${Math.max(12, Math.min(rect.left, innerWidth - width - 12))}px`,
        top: `${top}px`,
        width: `${width}px`,
        maxHeight: `${Math.min(height, innerHeight - top - 12)}px`,
      });
    };
    if (!node.matches(":popover-open")) node.showPopover();
    position();
    const outside = (e: PointerEvent) => {
      if (
        !node.contains(e.target as Node) &&
        !button.contains(e.target as Node)
      )
        setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        setOpen(false);
        button.focus();
      }
    };
    window.addEventListener("resize", position);
    document.addEventListener("scroll", position, true);
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", key, true);
    return () => {
      window.removeEventListener("resize", position);
      document.removeEventListener("scroll", position, true);
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", key, true);
      if (node.matches(":popover-open")) node.hidePopover();
    };
  }, [open]);
  return { open, setOpen, trigger, popup };
}
