import { Layout, Menu } from 'antd';
import {
  AppstoreOutlined,
  BarChartOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  FireOutlined,
  FundOutlined,
  RobotOutlined,
  SettingOutlined,
  StockOutlined,
  ThunderboltOutlined,
  LineChartOutlined,
} from '@ant-design/icons';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useEffect, type ReactNode } from 'react';
import StockSearch from '../StockSearch';
import RiskNotice from '../RiskNotice';
import { useAuthStore } from '../../store/authStore';
import { me } from '../../api/auth';

const { Header, Sider, Content, Footer } = Layout;

type NavItem = { key: string; label: string; icon?: ReactNode };

// Base nav (visible to everyone) + admin-only nav appended at render time.
const NAV: NavItem[] = [
  { key: '/market', label: '行情', icon: <BarChartOutlined /> },
  { key: '/stocks', label: '股票', icon: <StockOutlined /> },
  { key: '/sector', label: '板块', icon: <AppstoreOutlined /> },
  { key: '/dragon-tiger', label: '龙虎榜', icon: <FireOutlined /> },
  { key: '/factor', label: '因子', icon: <ExperimentOutlined /> },
  { key: '/strategy', label: '策略', icon: <ThunderboltOutlined /> },
  { key: '/backtest', label: '回测', icon: <LineChartOutlined /> },
  { key: '/portfolio', label: '组合', icon: <FundOutlined /> },
  { key: '/reports', label: '报告', icon: <FileTextOutlined /> },
  { key: '/assistant', label: '助手', icon: <RobotOutlined /> },
];

const ADMIN_NAV: NavItem = { key: '/admin', label: '管理', icon: <SettingOutlined /> };

export default function AppLayout() {
  const nav = useNavigate();
  const loc = useLocation();
  const { username, logout, role, token, setRole } = useAuthStore();

  // Keep the cached role honest: refresh from /me on every mount. Covers
  // sessions logged in before the role feature existed (null) AND stale
  // roles after a server-side promotion/demotion (e.g. 'user' → 'admin').
  useEffect(() => {
    if (!token) return;
    me()
      .then((p: { role?: string } | undefined) => setRole(p?.role ?? 'user'))
      .catch(() => undefined); // 401 redirects via the client interceptor
  }, [token, setRole]);

  const items = role === 'admin' ? [...NAV, ADMIN_NAV] : NAV;

  // Highlight the active nav even on detail routes (/stock/:code, /sector/:code).
  const selectedKey =
    items
      .map((i) => i.key)
      .filter((k) => loc.pathname === k || loc.pathname.startsWith(`${k}/`))
      .sort((a, b) => b.length - a.length)[0] ?? loc.pathname;

  return (
    <Layout style={{ height: '100vh' }}>
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          background: '#001529',
          flex: '0 0 64px',
        }}
      >
        <div
          style={{
            color: '#fff',
            fontWeight: 700,
            fontSize: 18,
            whiteSpace: 'nowrap',
          }}
        >
          AI Quant Platform
        </div>
        <div style={{ flex: 1, maxWidth: 480 }}>
          <StockSearch />
        </div>
        <div style={{ color: '#fff', marginLeft: 'auto' }}>
          {username ? username : '未登录'}
          {username && (
            <a
              onClick={() => {
                logout();
                nav('/login');
              }}
              style={{ color: '#ffa', marginLeft: 12 }}
            >
              退出
            </a>
          )}
        </div>
      </Header>
      <Layout style={{ flex: 1, minHeight: 0 }}>
        <Sider width={136} theme="dark">
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[selectedKey]}
            items={items}
            onClick={({ key }) => nav(key)}
          />
        </Sider>
        <Content
          style={{
            padding: 16,
            background: '#f0f2f5',
            overflow: 'auto',
            minHeight: 0,
          }}
        >
          <Outlet />
        </Content>
      </Layout>
      <Footer
        style={{
          padding: '8px 16px',
          textAlign: 'center',
          background: '#fffbe6',
          color: '#ad6800',
          borderTop: '1px solid #ffe58f',
          flex: '0 0 auto',
        }}
      >
        <RiskNotice />
      </Footer>
    </Layout>
  );
}
