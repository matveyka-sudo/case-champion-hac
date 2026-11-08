// Temporary validated view model. This is NOT the canonical API contract.
export interface ProfileSummary {
  id: string;
  version: string;
  timezone: string;
  cityIds: string[];
  modes: string[];
}
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Некорректный ответ профиля');
  return value as Record<string, unknown>;
}
function string(value: unknown): string {
  if (typeof value !== 'string' || !value.trim())
    throw new Error('Неполные данные профиля');
  return value;
}
function strings(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0)
    throw new Error('Неполные данные профиля');
  return value.map(string);
}
export function parseProfile(value: unknown): ProfileSummary {
  const data = record(value);
  if (data.status !== 'ready')
    throw new Error('Профиль не готов к использованию');
  return {
    id: string(data.profile_id),
    version: string(data.profile_version),
    timezone: string(data.timezone),
    cityIds: strings(data.city_ids),
    modes: strings(data.modes),
  };
}
