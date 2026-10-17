import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import ClarificationDemoPage from './adaptive/ClarificationDemoPage.tsx'

const isAdaptivePrototype =
  typeof window !== 'undefined' && window.location.pathname === '/adaptive-prototype'

createRoot(document.getElementById('root')!).render(
  <StrictMode>{isAdaptivePrototype ? <ClarificationDemoPage /> : <App />}</StrictMode>,
)
