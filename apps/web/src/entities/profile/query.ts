import { useQuery } from '@tanstack/react-query';
import { getJson } from '../../shared/api/http';
import { env } from '../../shared/config/env';
import { parseProfile } from './model';
export function useProfile() {
  return useQuery({
    queryKey: ['profile', env.profileId, env.profileVersion],
    queryFn: async ({ signal }) =>
      parseProfile(
        await getJson(
          `/api/v1/profiles/${encodeURIComponent(env.profileId)}/versions/${encodeURIComponent(env.profileVersion)}`,
          signal,
        ),
      ),
  });
}
