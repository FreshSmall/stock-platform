import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import AppLayout from './components/Layout/AppLayout';
import { useAuthStore } from './store/authStore';
import Market from './pages/Market';
import Stocks from './pages/Stocks';
import Sector from './pages/Sector';
import SectorDetail from './pages/SectorDetail';
import DragonTiger from './pages/DragonTiger';
import Admin from './pages/Admin';
import StockDetail from './pages/StockDetail';
import Analysis from './pages/Analysis';
import Strategy from './pages/Strategy';
import Backtest from './pages/Backtest';
import Assistant from './pages/Assistant';
import Factor from './pages/Factor';
import Portfolio from './pages/Portfolio';
import Reports from './pages/Reports';
import Login from './pages/Login';

const qc = new QueryClient();

function RequireAuth({ children }: { children: ReactElement }) {
  const token = useAuthStore((s) => s.token);
  return token ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          >
            <Route path="/market" element={<Market />} />
            <Route path="/stocks" element={<Stocks />} />
            <Route path="/sector" element={<Sector />} />
            <Route path="/sector/:code" element={<SectorDetail />} />
            <Route path="/dragon-tiger" element={<DragonTiger />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="/stock/:code" element={<StockDetail />} />
            <Route path="/analysis/:code" element={<Analysis />} />
            <Route path="/strategy" element={<Strategy />} />
            <Route path="/backtest" element={<Backtest />} />
            <Route path="/assistant" element={<Assistant />} />
            <Route path="/factor" element={<Factor />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/reports" element={<Reports />} />
            <Route path="/" element={<Navigate to="/market" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
