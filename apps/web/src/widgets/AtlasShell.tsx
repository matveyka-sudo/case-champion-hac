import type { ReactNode } from "react";

export function AtlasShell({ header, notice, children, className = "", label = "Атлас Waydex" }: {
  header: ReactNode; notice?: ReactNode; children: ReactNode; className?: string; label?: string;
}) {
  return <div id="atlas" className={`atlas ${className}`.trim()} aria-label={label}>
    {header}{notice}<div className="at-body">{children}</div>
  </div>;
}
