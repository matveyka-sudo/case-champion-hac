import { useState } from 'react';
import { useProfile } from '../entities/profile/query';
import { CitySelect } from '../features/select-city/CitySelect';
import { StatusPanel } from '../shared/ui/StatusPanel';
import { MapPanel } from '../widgets/map/MapPanel';
export function AtlasPage() {
  const profile = useProfile();
  const [selection, setSelection] = useState('');
  const city = profile.data?.cityIds.includes(selection)
    ? selection
    : profile.data?.cityIds[0];
  return (
    <>
      <h1 className="h3 mb-4">Транспортный атлас</h1>
      {profile.isPending && <StatusPanel title="Загружаем профиль оценки…" />}
      {profile.isError && (
        <StatusPanel error title="Не удалось получить профиль">
          <p>
            Проверьте доступность API и выбранную версию. Отсутствие данных не
            означает отсутствие маршрута.
          </p>
          <button
            className="btn btn-outline-danger"
            onClick={() => void profile.refetch()}
          >
            Повторить
          </button>
        </StatusPanel>
      )}
      <div className="row g-4">
        <aside className="col-12 col-lg-4">
          {profile.data && (
            <>
              <section className="border rounded p-3 mb-3">
                <h2 className="h5">Профиль {profile.data.id}</h2>
                <dl>
                  <dt>Ревизия</dt>
                  <dd>{profile.data.version}</dd>
                  <dt>Часовой пояс</dt>
                  <dd>{profile.data.timezone}</dd>
                  <dt>Виды транспорта</dt>
                  <dd>{profile.data.modes.join(', ')}</dd>
                </dl>
              </section>
              <CitySelect
                cities={profile.data.cityIds}
                value={city || ''}
                onChange={setSelection}
              />
            </>
          )}
          <p className="small text-body-secondary mt-3">
            Индексы, маршруты и сценарии будут получены из API. Браузер не
            рассчитывает транспортные показатели.
          </p>
        </aside>
        <div className="col-12 col-lg-8">
          <MapPanel city={city} />
        </div>
      </div>
    </>
  );
}
