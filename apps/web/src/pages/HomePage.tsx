export function HomePage() {
  return (
    <section className="py-5">
      <p className="text-body-secondary">Пилот · Калининградская область</p>
      <h1>Waydex</h1>
      <p className="lead">Индекс транспортной связанности</p>
      <p>
        Сравнение территорий, разбор поездок и оценка изменений транспортного
        сообщения.
      </p>
      <a className="btn btn-dark" href="#/atlas">
        Открыть атлас
      </a>
      <p className="small text-body-secondary mt-4">
        Техническая основа приложения. Визуальный дизайн и остальные сценарии
        будут подключены отдельно.
      </p>
    </section>
  );
}
