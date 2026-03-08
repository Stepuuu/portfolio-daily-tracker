import React, { Component, ErrorInfo } from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './styles/index.css'

class ErrorBoundary extends Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('[ErrorBoundary]', error, info) }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, fontFamily: 'monospace', background: '#1e1e2e', color: '#f38ba8', minHeight: '100vh' }}>
          <h2 style={{ color: '#cba6f7' }}>React 渲染错误</h2>
          <pre style={{ whiteSpace: 'pre-wrap', color: '#fab387' }}>{String((this.state.error as Error).message)}</pre>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: '#a6adc8', marginTop: 16 }}>{String((this.state.error as Error).stack)}</pre>
        </div>
      )
    }
    return this.props.children
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30000,
      refetchOnWindowFocus: false,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
)
