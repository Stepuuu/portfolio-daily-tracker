import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import Header from './Header'

export default function Layout() {
  return (
    <div className="flex h-screen bg-slate-900">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 min-w-0 overflow-auto p-2 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
