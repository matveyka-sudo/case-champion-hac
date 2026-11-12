import { timestampText } from "../api/adapters/result";
import type { Schemas } from "../api/types";

const labels: Record<string, string> = {
  plane: "Самолёт", train: "Поезд", suburban: "Электричка", bus: "Автобус",
  water: "Судно", helicopter: "Вертолёт",
};
const modes: Record<string, string> = {
  plane: "air", helicopter: "air", train: "rail", suburban: "rail", bus: "bus", water: "sea",
};

export function PassengerSchedule({ schedule, selectedModes, day, departure, fromTimezone, toTimezone }: {
  schedule: Schemas["PassengerScheduleObservation"];
  selectedModes: string[];
  day: string;
  departure: string | null;
  fromTimezone?: string;
  toTimezone?: string;
}) {
  const dateCovered = departure !== null && schedule.completed_dates.includes(day);
  const rows = dateCovered ? schedule.departures
    .filter((row) => selectedModes.includes(modes[row.transport_type]) &&
      Date.parse(row.departure_at) >= Date.parse(departure))
    .sort((a, b) => Date.parse(a.departure_at) - Date.parse(b.departure_at))
    .slice(0, 6) : [];
  return <details open={rows.length > 0}>
    <summary>Ближайшие рейсы</summary>
    <p className="fhint">Прямые рейсы по расписанию. Время указано местное для каждой станции.</p>
    {rows.length ? <ul>{rows.map((row) => (
      <li key={JSON.stringify([row.service_uid, row.departure_at, row.from_station.code, row.to_station.code])}>
        <strong>{labels[row.transport_type]}</strong>
        <div>{row.from_station.title} · {timestampText(row.departure_at, fromTimezone)}</div>
        <div>{row.to_station.title} · {timestampText(row.arrival_at, toTimezone)}</div>
      </li>
    ))}</ul> : <p className="fhint">{dateCovered
      ? "В полученном расписании после выбранного времени прямых рейсов этого вида не найдено. Пересадки в этой выдаче не проверялись."
      : "На выбранную дату нет загруженного расписания."}</p>}
    <p><a href="https://rasp.yandex.ru/" target="_blank" rel="noreferrer">Данные предоставлены сервисом Яндекс.Расписания</a></p>
    <iframe title="Яндекс Расписания" src={schedule.copyright_banner_url} width={240} height={130}
      sandbox="" style={{ border: 0, maxWidth: "100%" }} />
  </details>;
}
