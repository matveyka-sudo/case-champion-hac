import { env } from '../config/env';
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
export async function getJson(
  path: string,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(`${env.apiBaseUrl}${path}`, {
    signal,
    headers: { Accept: 'application/json' },
  });
  if (!response.ok)
    throw new ApiError(response.status, `API вернул ошибку ${response.status}`);
  return response.json();
}
