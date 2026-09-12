import { lazy, Suspense } from 'react'
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
const Dashboard = lazy(() => import('./pages/Dashboard'))
const Ledger = lazy(() => import('./pages/Ledger'))
const Portfolio = lazy(() => import('./pages/Portfolio'))
const PortfolioTracker = lazy(() => import('./pages/PortfolioTracker'))
const Market = lazy(() => import('./pages/Market'))
const Memory = lazy(() => import('./pages/Memory'))
const Settings = lazy(() => import('./pages/Settings'))
const Help = lazy(() => import('./pages/Help'))
const Backtest = lazy(() => import('./pages/Backtest'))
const ResearchLab = lazy(() => import('./pages/ResearchLab'))

function App() {
  return (
    <Router>
      <Suspense fallback={<div role="status" className="p-6 text-slate-400">正在加载…</div>}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="ledger" element={<Ledger />} />
          <Route path="portfolio" element={<Portfolio />} />
          <Route path="tracker" element={<PortfolioTracker />} />
          <Route path="market" element={<Market />} />
          <Route path="memory" element={<Memory />} />
          <Route path="backtest" element={<Backtest />} />
          <Route path="lab" element={<ResearchLab />} />
          <Route path="settings" element={<Settings />} />
          <Route path="help" element={<Help />} />
        </Route>
      </Routes>
      </Suspense>
    </Router>
  )
}

export default App
