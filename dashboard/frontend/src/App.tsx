import { Suspense } from 'react'
import { BrowserRouter as Router, Link, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { EmptyState } from '@/components/ui'
import { AppShell, PageLoading, RoutePage } from '@/components/shell/AppShell'
import { resolveRedirect } from '@/lib/navigation'
import { hiddenRoutes, navPages, redirects } from './routes'

function NotFound() {
  return (
    <EmptyState
      title="页面不存在"
      description="这个地址没有对应的页面，可能已经移动。"
      action={<Link to="/" className="text-body text-accent underline-offset-4 hover:underline">回到今日</Link>}
    />
  )
}

/** An old path: on to its new home, with the query string and hash it came with. */
function LegacyRedirect({ to }: { to: string }) {
  const { pathname, search, hash } = useLocation()
  return <Navigate to={resolveRedirect(redirects, pathname, search, hash) ?? to} replace />
}

// The route table (src/routes.tsx) is the only list of pages: everything below is built from it.
function App() {
  return (
    <Router>
      <Routes>
        {hiddenRoutes.filter((route) => route.bare).map((route) => (
          <Route key={route.path} path={route.path} element={<Suspense fallback={<PageLoading />}><route.element /></Suspense>} />
        ))}
        <Route element={<AppShell />}>
          {[...navPages, ...hiddenRoutes.filter((route) => !route.bare)].map((route) => (
            <Route key={route.path} path={route.path} element={<RoutePage route={route}><route.element /></RoutePage>} />
          ))}
          {redirects.map((redirect) => (
            <Route key={redirect.from} path={redirect.from} element={<LegacyRedirect to={redirect.to} />} />
          ))}
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </Router>
  )
}

export default App
