import type { ReactNode, RefObject } from "react";
import { usePopover } from "../shared/usePopover";
import { NavigationLink } from "../shared/NavigationLink";
import { Icon, Wordmark } from "../shared/ui";
export type AtlasMode = "city" | "pax" | "cargo";
export type AtlasTheme = "auto" | "light" | "dark";

export function AtlasHeader({ mode, title, theme, onModeChange, onThemeChange, onFilters, chatOpen, onChat, controls, showChat = true,
  life = false, onLife, filtersButtonRef, chatButtonRef }: {
  life?: boolean; onLife?: () => void; mode: AtlasMode; title: string; theme: AtlasTheme; onModeChange: (mode: AtlasMode) => void;
  onThemeChange: (theme: AtlasTheme) => void; onFilters: () => void; chatOpen: boolean; onChat: () => void;
  controls?: ReactNode; showChat?: boolean; filtersButtonRef?: RefObject<HTMLButtonElement | null>; chatButtonRef?: RefObject<HTMLButtonElement | null>;
}) {
  const { open, setOpen, trigger, popup } = usePopover();
  return <header className="at-top">
    <NavigationLink className="at-back" to="top" aria-label="Вернуться на главную"><Icon name="arrow-left" /></NavigationLink>
    <Wordmark small />
    <nav className="modes product-modes" aria-label="Разделы Waydex">
      <button aria-pressed={life} onClick={onLife || (() => { window.location.hash = "atlas/address"; })}><Icon name="map-pin" /><span>Для жизни</span></button>
      <button ref={trigger} aria-pressed={!life} aria-expanded={open} aria-controls="connections-menu" onClick={() => setOpen(!open)}><Icon name="waypoints" /><span>Транспортные связи</span><Icon name="chevron-down" /></button>
      <div ref={popup} id="connections-menu" popover="manual" className="wx-popup wx-select-popup connections-menu" aria-label="Транспортные связи">
        {([["city", "building-2", "В городе"], ["pax", "waypoints", "Пассажирские связи"], ["cargo", "container", "Грузовые связи"]] as const).map(([value, icon, label]) =>
          <button key={value} aria-pressed={!life && mode === value} onClick={() => { onModeChange(value); setOpen(false); trigger.current?.focus(); }}><Icon name={icon} /><span>{label}</span>{!life && mode === value && <Icon name="check" />}</button>)}
      </div>
    </nav>
    <div className="crumbs"><Icon name="map" /><span>Россия</span>{title !== "Россия · выберите город" && <><Icon name="chevron-right" /><b>{title}</b></>}</div>
    <div className="at-sp" />
    <div className="theme-seg" role="group" aria-label="Тема оформления">{(["auto", "light", "dark"] as const).map((value, index) =>
      <button key={value} aria-label={`Тема: ${["авто", "светлая", "тёмная"][index]}`} aria-pressed={theme === value}
        title={["Авто: по времени на шкале", "Светлая", "Тёмная"][index]} onClick={() => onThemeChange(value)}><Icon name={["sun-moon", "sun", "moon"][index]} /></button>)}</div>
    {controls}
    <button ref={filtersButtonRef} className="iconbtn m-only" aria-label="Фильтры" onClick={onFilters}><Icon name="sliders-horizontal" /></button>
    {showChat && <button ref={chatButtonRef} className="iconbtn" id="chatToggle" aria-label="Агент" aria-pressed={chatOpen} aria-expanded={chatOpen} aria-controls="chat" onClick={onChat}>
      <Icon name="sparkles" /><span className="d-only">Агент</span>
    </button>}
  </header>;
}
