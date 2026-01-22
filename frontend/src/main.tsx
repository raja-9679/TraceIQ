import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { Toaster } from 'sonner';

console.log("MODE:", import.meta.env.MODE);
console.log("PROD:", import.meta.env.PROD);
console.log("DEV:", import.meta.env.DEV);


createRoot(document.getElementById('root')!).render(

  <StrictMode>
    <App />
    <Toaster richColors />
  </StrictMode>,
)
