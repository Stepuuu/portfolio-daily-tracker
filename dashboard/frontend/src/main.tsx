import React, { Component, ErrorInfo } from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { followThemePreference } from './lib/theme'
import './styles/index.css'

// index.html chose the theme before the first paint; from here a 跟随系统 preference follows the system.
followThemePreference()

class ErrorBoundary extends Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('[ErrorBoundary]', error, info) }
  render() {
    if (this.state.error) {
      // Outside the router and the kit (either may be what failed): plain elements on the theme tokens.
      return (
        <div role="alert" className="min-h-screen bg-bg p-8 text-fg">
          <h1 className="text-page">页面出错了</h1>
          <p className="mt-2 text-body text-fg-2">这一页没能显示。刷新页面再试；问题持续时，把下面的错误信息附在问题反馈里。</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-4 h-8 rounded-md bg-accent px-3 text-body font-medium text-accent-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            刷新页面
          </button>
          <pre className="mt-6 whitespace-pre-wrap break-words text-table text-warn">{String((this.state.error as Error).message)}</pre>
          <pre className="mt-4 whitespace-pre-wrap break-words text-caption text-fg-3">{String((this.state.error as Error).stack)}</pre>
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
