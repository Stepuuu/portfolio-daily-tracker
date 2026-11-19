// The wizard's queries: the checklist (with the quote probe) and a refresh after anything was saved.
import { useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { SETUP_KEY, SETUP_STATUS_KEY, fetchSetupStatus } from '@/services/setup'

/** `GET /api/setup/status`, read again when the window regains focus (a .env edit, a restarted backend). */
export function useSetupStatus() {
  return useQuery({ queryKey: SETUP_STATUS_KEY, queryFn: () => fetchSetupStatus(true), staleTime: 5_000 })
}

/** Read the checklist (and the model settings) again, e.g. after a confirmation or a save. */
export function useRefreshSetup(): () => Promise<void> {
  const client = useQueryClient()
  return useCallback(() => client.invalidateQueries({ queryKey: SETUP_KEY }), [client])
}
