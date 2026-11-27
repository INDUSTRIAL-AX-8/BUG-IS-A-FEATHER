import { Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import { Layout } from './components/Layout'
import { ControlChartPage } from './pages/ControlChartPage'
import { DashboardPage } from './pages/DashboardPage'
import { DefectPage } from './pages/DefectPage'
import { GenealogyPage } from './pages/GenealogyPage'
import { LoginPage } from './pages/LoginPage'
import { AccuracyPage } from './pages/AccuracyPage'
import { MasterPage } from './pages/MasterPage'
import { Placeholder } from './pages/Placeholder'
import { DriversPage } from './pages/DriversPage'
import { TermsProvider } from './lib/terms'
import { LiveProvider } from './lib/live'
import { LotsPage } from './pages/LotsPage'
import { ReviewPage } from './pages/ReviewPage'
import { ProductionPage } from './pages/ProductionPage'
import { SettingsPage } from './pages/SettingsPage'
import { SimulationPage } from './pages/SimulationPage'
import { UploadPage } from './pages/UploadPage'

export function App() {
  const { user, ready } = useAuth()
  if (!ready) return <div className="empty">불러오는 중…</div>
  if (!user) return <LoginPage />
  return (
    <TermsProvider>
    <LiveProvider>
    <Layout>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/spc" element={<ControlChartPage />} />
        <Route path="/genealogy" element={<GenealogyPage />} />
        <Route path="/drivers" element={<DriversPage />} />
        <Route path="/lots" element={<LotsPage />} />
        <Route path="/review" element={<ReviewPage />} />
        <Route path="/production" element={<ProductionPage />} />
        <Route path="/defects" element={<DefectPage />} />
        <Route path="/master" element={<MasterPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/accuracy" element={<AccuracyPage />} />
        <Route path="/simulation" element={<SimulationPage />} />
        <Route path="*" element={<Placeholder title="없는 화면" />} />
      </Routes>
    </Layout>
    </LiveProvider>
    </TermsProvider>
  )
}
