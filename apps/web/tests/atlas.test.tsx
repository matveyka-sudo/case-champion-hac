import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AtlasPage } from '../src/pages/AtlasPage';
import { parseProfile } from '../src/entities/profile/model';
const data = {
  status: 'ready',
  profile_id: 'P1',
  profile_version: '1.0.0',
  timezone: 'Europe/Kaliningrad',
  city_ids: ['city-a', 'city-b'],
  modes: ['rail', 'bus', 'walk'],
};
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AtlasPage />
    </QueryClientProvider>,
  );
}
afterEach(() => vi.unstubAllGlobals());
it('shows loading without invented scores', () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise(() => {})),
  );
  mount();
  expect(screen.getByText('Загружаем профиль оценки…')).not.toBeNull();
});
it('loads profile and changes selected city', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(new Response(JSON.stringify(data))),
  );
  mount();
  expect(await screen.findByText('Europe/Kaliningrad')).not.toBeNull();
  fireEvent.change(screen.getByLabelText('Населённый пункт'), {
    target: { value: 'city-b' },
  });
  expect(screen.getByText('Выбран населённый пункт: city-b')).not.toBeNull();
});
it('shows API error and allows retry', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response('', { status: 503 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(data)));
  vi.stubGlobal('fetch', fetcher);
  mount();
  expect((await screen.findByRole('alert')).textContent).toContain(
    'Не удалось получить профиль',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(await screen.findByText('Europe/Kaliningrad')).not.toBeNull();
});
it('rejects malformed or non-ready profiles', () => {
  expect(() => parseProfile({ ...data, city_ids: [] })).toThrow();
  expect(() => parseProfile({ ...data, status: 'invalid_profile' })).toThrow();
});
