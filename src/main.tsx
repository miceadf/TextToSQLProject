import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import AdaptivePrototypePage from './adaptive-prototype/AdaptivePrototypePage.tsx'

const isAdaptivePrototype =
  typeof window !== 'undefined' && window.location.pathname === '/adaptive-prototype'

createRoot(document.getElementById('root')!).render(
  <StrictMode>{isAdaptivePrototype ? <AdaptivePrototypePage /> : <App />}</StrictMode>,
)
