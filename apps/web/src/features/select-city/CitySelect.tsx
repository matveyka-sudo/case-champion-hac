export function CitySelect({
  cities,
  value,
  onChange,
}: {
  cities: string[];
  value: string;
  onChange: (city: string) => void;
}) {
  return (
    <div>
      <label className="form-label" htmlFor="city">
        Населённый пункт
      </label>
      <select
        id="city"
        className="form-select"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {cities.map((city) => (
          <option key={city} value={city}>
            {city}
          </option>
        ))}
      </select>
      <p className="form-text">
        Названия и координаты подключим из версионированного каталога. Сейчас
        показаны идентификаторы API.
      </p>
    </div>
  );
}
