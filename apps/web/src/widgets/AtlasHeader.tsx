import type { ReactNode, RefObject } from "react";
import { NavigationLink } from "../shared/NavigationLink";
import { Icon, Wordmark } from "../shared/ui";
export type AtlasMode = "city" | "pax" | "cargo";
export type AtlasTheme = "auto" | "light" | "dark";

export function AtlasHeader({ mode, title, theme, onModeChange, onThemeChange, onFilters, chatOpen, onChat, controls,
  filtersButtonRef, chatButtonRef }: {
  mode: AtlasMode; title: string; theme: AtlasTheme; onModeChange: (mode: AtlasMode) => void;
  onThemeChange: (theme: AtlasTheme) => void; onFilters: () => void; chatOpen: boolean; onChat: () => void;
  controls?: ReactNode; filtersButtonRef?: RefObject<HTMLButtonElement | null>; chatButtonRef?: RefObject<HTMLButtonElement | null>;
}) {
  return <header className="at-top">
    <NavigationLink className="at-back" to="top" aria-label="Вернуться на главную"><Icon name="arrow-left" /></NavigationLink>
    <Wordmark small />
    <div className="modes" role="tablist" aria-label="Режим анализа" id="modeTabs" onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const modes: AtlasMode[] = ["city", "pax", "cargo"];
      const next = modes[(modes.indexOf(mode) + (event.key === "ArrowRight" ? 1 : 2)) % 3];
      onModeChange(next); document.getElementById(`mode-${next}`)?.focus();
    }}>
      {([ ["city", "building-2", "В городе", "В городе"], ["pax", "waypoints", "Пассажирские связи", "Пассажиры"],
        ["cargo", "container", "Грузовые связи", "Грузы"] ] as const).map(([value, icon, long, short]) =>
        <button id={`mode-${value}`} key={value} role="tab" aria-selected={mode === value} tabIndex={mode === value ? 0 : -1} onClick={() => onModeChange(value)}>
          <Icon name={icon} /><span className="lbl"><span className="l-full">{long}</span><span className="l-short">{short}</span></span>
        </button>)}
    </div>
    <div className="crumbs"><Icon name="map" /><span>Россия</span>{title !== "Россия · выберите город" && <><Icon name="chevron-right" /><b>{title}</b></>}</div>
    <div className="at-sp" />
    <div className="theme-seg" role="group" aria-label="Тема оформления">{(["auto", "light", "dark"] as const).map((value, index) =>
      <button key={value} aria-label={`Тема: ${["авто", "светлая", "тёмная"][index]}`} aria-pressed={theme === value}
        title={["Авто: по времени на шкале", "Светлая", "Тёмная"][index]} onClick={() => onThemeChange(value)}><Icon name={["sun-moon", "sun", "moon"][index]} /></button>)}</div>
    {controls}
    <button ref={filtersButtonRef} className="iconbtn m-only" aria-label="Фильтры" onClick={onFilters}><Icon name="sliders-horizontal" /></button>
    <button ref={chatButtonRef} className="iconbtn" id="chatToggle" aria-label="Агент" aria-pressed={chatOpen} aria-expanded={chatOpen} aria-controls="chat" onClick={onChat}>
      <Icon name="sparkles" /><span className="d-only">Агент</span>
    </button>
  </header>;
}
