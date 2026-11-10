import { Fragment, type CSSProperties, type ReactNode, type RefObject } from "react";
import { Icon } from "../shared/ui";
export function ResultCardFrame({ children, compact = false, id, sectionRef }: { children: ReactNode; compact?: boolean; id?: string; sectionRef?: RefObject<HTMLElement | null> }) {
  return <section className={`ctx panelc ${compact ? "compact" : ""}`} aria-label="Выбранный объект" id={id} ref={sectionRef}>{children}</section>;
}
export function ResultCardHeader({ label, title, onClose, titleRef }: { label: ReactNode; title: ReactNode; onClose: () => void; titleRef?: RefObject<HTMLHeadingElement | null> }) {
  return <div className="ctx-h"><div style={{ minWidth: 0 }}><span className="k">{label}</span><h3 ref={titleRef} tabIndex={titleRef ? -1 : undefined}>{title}</h3></div>
    <button className="ctx-x" aria-label="Закрыть карточку" onClick={onClose}><Icon name="x" /></button></div>;
}
export function ResultPrimary({ time, score, pending, caption, qualification }: {
  time?: ReactNode; score?: { value: ReactNode; color: string; title?: string; band?: string }; pending?: ReactNode; caption?: ReactNode; qualification?: ReactNode;
}) {
  return <><div className="bigrow">
    {time != null && <span className="bignum tnum">{time}<small>мин</small></span>}
    {pending || (score && <span className="idxpill tnum" title={score.title} data-score-band={score.band}><i style={{ "--c": score.color } as CSSProperties} />{score.value}</span>)}
    <span className="bigcap">{caption}</span>
  </div>{qualification && <p className="fhint">{qualification}</p>}</>;
}
export interface RouteSegmentView { kind: string; minutes: number; label: string; valueText?: string; barText?: string }
const segmentColors: Record<string, string> = { walk: "#8C93BD", wait: "#B4A4FF", ride: "#2F5BFF", ride2: "#7A52FF", xfer: "#5D6390", hot: "#7A52FF" };
export function RouteBreakdown({ segments }: { segments: RouteSegmentView[] }) {
  return <><div className="mbar" role="img" aria-label={segments.map((segment) => `${segment.label} ${segment.valueText ?? segment.minutes} мин`).join(", ")}>
    {segments.map((segment, index) => <span key={index} className={`s ${segment.kind}`} style={{ "--m": segment.minutes } as CSSProperties}
      title={`${segment.label} · ${segment.valueText ?? segment.minutes} мин`}>{segment.minutes >= 4 ? segment.barText ?? segment.valueText ?? segment.minutes : ""}</span>)}
  </div><div className="mlist tnum">{segments.map((segment, index) => <Fragment key={index}>
    <i className={`k mbar-${segment.kind}`} style={{ background: segmentColors[segment.kind] }} />
    <span className={segment.kind === "hot" ? "hot" : ""}>{segment.label}</span><b className={segment.kind === "hot" ? "hot" : ""}>{segment.valueText ?? segment.minutes} мин</b>
  </Fragment>)}</div></>;
}
export function ResultFigure({ title, value, wide = false }: { title: string; value: ReactNode; wide?: boolean }) {
  return <div className="kv" style={wide ? { gridColumn: "1/-1" } : undefined}><span>{title}</span><b>{value}</b></div>;
}
export function ResultCategoryTable({ rows, overall }: { rows: { id: string; label: string; priority?: string; time: ReactNode; value: ReactNode }[]; overall?: ReactNode }) {
  return <table className="ctable tnum"><tbody>{rows.map((row) => <tr key={row.id}><td>{row.label}{row.priority && <span className="p">{row.priority}</span>}</td>
    <td style={{ textAlign: "right", color: "var(--text-3)" }}>{row.time}</td><td>{row.value}</td></tr>)}
    {overall !== undefined && <tr><td><b>Общий индекс</b></td><td /><td>{overall}</td></tr>}
  </tbody></table>;
}
