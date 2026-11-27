import { Route, Routes } from 'react-router-dom'
import { useAuth } from './auth'
import { Layout } from './components/Layout'
import { ControlChartPage } from './pages/ControlChartPage'
import { DashboardPage } from './pages/DashboardPage'
import { DefectPage } from './pages/DefectPage'
import { GenealogyPage } from './pages/GenealogyPage'
import { LoginPage } from './pages/LoginPage'
import { Placeholder } from './pages/Placeholder'

export function App() {
  const { user, ready } = useAuth()
  if (!ready) return <div className="empty">불러오는 중…</div>
  if (!user) return <LoginPage />
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/spc" element={<ControlChartPage />} />
        <Route path="/genealogy" element={<GenealogyPage />} />
        <Route path="/defects" element={<DefectPage />} />
        <Route path="/master" element={<Placeholder title="마스터 관리" />} />
        <Route path="/upload" element={<Placeholder title="데이터 업로드" />} />
        <Route path="/settings" element={<Placeholder title="설정" />} />
        <Route path="/accuracy" element={<Placeholder title="정확도 지표" />} />
        <Route path="/simulation" element={<Placeholder title="3D 시뮬레이션" />} />
        <Route path="*" element={<Placeholder title="없는 화면" />} />
      </Routes>
    </Layout>
  )
}
