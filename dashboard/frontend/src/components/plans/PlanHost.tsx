// Mount once on a page that opens plans (Plans, Today): renders the drawer and the form.
import { PlanDrawer } from './PlanDrawer'
import { PlanFormDialog } from './PlanFormDialog'

export function PlanHost() {
  return (
    <>
      <PlanDrawer />
      <PlanFormDialog />
    </>
  )
}
