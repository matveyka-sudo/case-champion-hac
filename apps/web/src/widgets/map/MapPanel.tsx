export function MapPanel({ city }: { city?: string }) {
  return (
    <section
      className="map-placeholder border rounded p-4"
      aria-label="Картографическая область"
    >
      <h2 className="h5">Карта связности</h2>
      <p>
        {city
          ? `Выбран населённый пункт: ${city}`
          : 'Выберите населённый пункт после загрузки профиля.'}
      </p>
      <p className="text-body-secondary">
        Карта появится после подключения подтверждённых координат и транспортных
        связей. Расчётных результатов пока нет.
      </p>
    </section>
  );
}
