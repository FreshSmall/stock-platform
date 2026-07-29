import { Layout, Menu } from 'antd';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import StockSearch from '../StockSearch';
import RiskNotice from '../RiskNotice';
import { useAuthStore } from '../../store/authStore';

const { Header, Sider, Content, Footer } = Layout;

const NAV = [
  { key: '/market', label: '行情' },
  { key: '/strategy', label: '策略' },
  { key: '/backtest', label: '回测' },
  { key: '/assistant', label: '助手' },
];

export default function AppLayout() {
  const nav = useNavigate();
  const loc = useLocation();
  const { username, logout } = useAuthStore();

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
        <Sider width={120} theme="dark">
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[loc.pathname]}
            items={NAV}
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
