// Which plan is open and which form is showing, outside the React tree so any page can open them:
// the Today chip opens a plan's drawer, a page may open the form with a ticker filled in.
// `PlanHost` (components/plans) renders them; mount it once on a page that uses this store.
import { create } from 'zustand'
import type { PlanForm } from './form'

export type PlanFormRequest = { mode: 'new'; preset?: Partial<PlanForm> } | { mode: 'edit'; id: number }

interface PlanUiState {
  viewId: number | null
  form: PlanFormRequest | null
  openPlan: (id: number) => void
  closePlan: () => void
  openForm: (request: PlanFormRequest) => void
  closeForm: () => void
}

export const usePlanUi = create<PlanUiState>((set) => ({
  viewId: null,
  form: null,
  openPlan: (id) => set({ viewId: id }),
  closePlan: () => set({ viewId: null }),
  openForm: (request) => set({ form: request }),
  closeForm: () => set({ form: null }),
}))

/** Open a plan's drawer from anywhere (a page that mounts `PlanHost`): `openPlanDrawer(12)`. */
export const openPlanDrawer = (id: number) => usePlanUi.getState().openPlan(id)
