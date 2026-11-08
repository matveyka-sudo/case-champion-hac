import { iconRegistry } from "./iconRegistry";
import type { ReactNode } from "react";
export function Icon({
  name,
  className = "",
}: {
  name: string;
  className?: string;
}) {
  const Component = iconRegistry[name] || iconRegistry["circle-help"];
  return (
    <Component
      className={`lucide ${className}`}
      aria-hidden="true"
      strokeWidth={1.9}
    />
  );
}
export function Wordmark({ small = false }: { small?: boolean }) {
  return (
    <a
      className={`wordmark ${small ? "sm" : ""}`}
      href="#top"
      aria-label="Waydex — на главную"
    >
      <span className="wm-way">Way</span>
      <span className="wm-dex">dex</span>
    </a>
  );
}
export function Demo() {
  return <span className="demo-tag">демо</span>;
}
export function Notice({
  children,
  kind = "nd",
}: {
  children: ReactNode;
  kind?: string;
}) {
  return (
    <div className={`stmsg ${kind}`}>
      <Icon name={kind === "err" ? "triangle-alert" : "info"} />
      <span>{children}</span>
    </div>
  );
}
