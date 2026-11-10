import type { ReactNode } from "react";
import { Icon } from "../shared/ui";
export function FiltersPanelView({ collapsed, mobileOpen, onClose, onToggle, onExpand, railIcons, children, footer, id = "filters", mobile = false, notice }: {
  collapsed: boolean; mobileOpen: boolean; onClose: () => void; onToggle: () => void; onExpand: () => void;
  railIcons: string[]; children: ReactNode; footer: ReactNode; id?: string;
  mobile?: boolean; notice?: ReactNode;
}) {
  return <aside className={`at-filters ${collapsed ? "is-collapsed" : ""} ${mobileOpen ? "m-open" : ""}`} id={id} aria-label="Фильтры и условия" inert={mobile && !mobileOpen}>
    <div className="m-head"><b>Фильтры</b><button className="ctx-x" aria-label="Закрыть фильтры" onClick={onClose}><Icon name="x" /></button></div>
    <div className="f-head"><h2>Условия</h2><button className="ctx-x d-only" aria-label={collapsed ? "Развернуть фильтры" : "Свернуть фильтры"}
      aria-expanded={!collapsed} onClick={onToggle}><Icon name={collapsed ? "panel-left-open" : "panel-left"} /></button></div>
    <div className="f-rail">{railIcons.map((icon) => <button className="zbtn" key={icon} aria-label="Развернуть условия" onClick={onExpand}><Icon name={icon} /></button>)}</div>
    <div className="f-scroll">{notice && <div className="stmsg unk" role="note"><strong>{notice}</strong></div>}{children}</div><div className="f-foot">{footer}</div>
  </aside>;
}
