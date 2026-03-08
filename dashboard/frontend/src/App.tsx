import { BrowserRouter as Router, Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Portfolio from './pages/Portfolio'
import PortfolioTracker from './pages/PortfolioTracker'
import Market from './pages/Market'
import Memory from './pages/Memory'
import Settings from './pages/Settings'
import Help from './pages/Help'
import Backtest from './pages/Backtest'

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="portfolio" element={<Portfolio />} />
          <Route path="tracker" element={<PortfolioTracker />} />
          <Route path="market" element={<Market />} />
          <Route path="memory" element={<Memory />} />
          <Route path="backtest" element={<Backtest />} />
          <Route path="settings" element={<Settings />} />
          <Route path="help" element={<Help />} />
        </Route>
      </Routes>
    </Router>
  )
}

export default App
