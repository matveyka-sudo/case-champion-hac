import type { CSSProperties, ReactNode, RefObject } from "react";
import { Icon } from "../shared/ui";
export function MapViewport({ children, className = "", mapRef }: { children: ReactNode; className?: string; mapRef?: RefObject<HTMLElement | null> }) {
  return <section className={`at-map ${className}`} id="mapArea" ref={mapRef} aria-label="Карта">{children}</section>;
}
export function MapCanvas({ children }: { children: ReactNode }) { return <div className="map-wrapper">{children}</div>; }
export function MapOverlay({ position, children }: { position: "tl" | "r" | "bottom" | "center"; children: ReactNode }) {
  return <div className={`ov ov-${position}`}>{children}</div>;
}
export function AtlasStatus({ kind, title, subtitle, actions, comparison }: { kind: string; title: ReactNode; subtitle: ReactNode; actions?: ReactNode; comparison?: ReactNode }) {
  return <div className="status panelc" data-s={kind} role="status" aria-live="polite"><span className="sdot" aria-hidden="true" />
    <span className="st"><b>{title}</b><span>{subtitle}</span></span>{actions}{comparison}</div>;
}
export function StateCard({ icon, title, description, actions, loading = false, role = "status" }: { icon: string; title: ReactNode; description: ReactNode; actions?: ReactNode; loading?: boolean; role?: "status" | "alert" }) {
  return <MapOverlay position="center"><div className="statecard panelc" role={role}><span className="si"><Icon name={icon} className={loading ? "spin" : ""} /></span>
    <h3>{title}</h3><p>{description}</p>{loading ? <div className="progress"><i /></div> : <div className="row-act">{actions}</div>}</div></MapOverlay>;
}
export function MapControls({ items, onAction }: { items: string[][]; onAction: (kind: string) => void }) {
  return <div className="zgroup panelc" role="group" aria-label="Масштаб карты">{items.map(([kind, icon, label]) =>
    <button className="zbtn" key={kind} aria-label={label} title={label} onClick={() => onAction(kind)}><Icon name={icon} /></button>)}</div>;
}
export function AtlasLegend({ title, scale, open, onToggle, ticks, note, details, items, barStyle }: {
  title: string; scale: boolean; open: boolean; onToggle: () => void; ticks: ReactNode[]; note?: ReactNode; details?: ReactNode; items?: ReactNode; barStyle?: CSSProperties;
}) {
  return <div className={`legend panelc ${scale && open ? "open" : ""}`}><h3><span className="t" title={title}>{title}</span>{scale &&
    <button className="lg-toggle" aria-expanded={open} aria-label="Подробнее о легенде" onClick={onToggle}><Icon name={open ? "chevron-down" : "info"} /></button>}</h3>
    {scale ? <><div className="lg-bar" style={barStyle} /><div className="lg-ticks tnum">{ticks.map((tick, index) => <span key={index}>{tick}</span>)}</div>
      {note && <p className="lg-note" style={{ marginTop: 6 }}>{note}</p>}{open && <div className="lg-more panelc">{details}</div>}</> : <div className="lg-items" style={{ marginTop: 10 }}>{items}</div>}
  </div>;
}
export function AtlasMobileSheet({ title, description, tag, timeDock, onFilters, onChat, sheetRef, children }: {
  title: ReactNode; description: ReactNode; tag?: ReactNode; timeDock: ReactNode; onFilters: () => void; onChat: () => void; sheetRef?: RefObject<HTMLDivElement | null>; children?: ReactNode;
}) {
  return <div className="m-sheet" ref={sheetRef}><span className="grab" aria-hidden="true" /><div className="m-sum"><div><b>{title}</b><span>{description}</span></div>{tag}</div>
    {children}{timeDock}<div className="m-btns"><button className="iconbtn" onClick={onFilters}><Icon name="sliders-horizontal" />Фильтры</button>
      <button className="iconbtn primary" onClick={onChat}><Icon name="sparkles" />Агент</button></div></div>;
}
