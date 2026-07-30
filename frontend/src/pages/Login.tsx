import { useState } from 'react';
import { Card, Form, Input, Button, Tabs, message } from 'antd';
import { useNavigate } from 'react-router-dom';
import { login, me, register } from '../api/auth';
import { useAuthStore } from '../store/authStore';

export default function Login() {
  const nav = useNavigate();
  const setAuth = useAuthStore((s) => s.setAuth);
  const setRole = useAuthStore((s) => s.setRole);
  const [mode, setMode] = useState('login');
  const [loading, setLoading] = useState(false);

  const onFinish = async (vals: { username: string; password: string }) => {
    setLoading(true);
    try {
      if (mode === 'login') {
        const r: any = await login(vals.username, vals.password);
        setAuth(r.token, r.user.username);
        // Resolve the caller's role (V1.5) so the admin nav gates correctly.
        // The /me endpoint may not yet return `role` for older backends; in
        // that case we default to 'user' so a non-admin session is never
        // accidentally elevated.
        try {
          const profile = await me();
          setRole(profile?.role ?? 'user');
        } catch {
          setRole('user');
        }
        nav('/market');
      } else {
        await register(vals.username, vals.password);
        message.success('注册成功，请登录');
        setMode('login');
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : '操作失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 380, margin: '80px auto' }}>
      <Card title="AI Quant Platform">
        <Tabs
          activeKey={mode}
          onChange={setMode}
          items={[
            { key: 'login', label: '登录' },
            { key: 'register', label: '注册' },
          ]}
        />
        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, min: 6 }]}
          >
            <Input.Password />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            {mode === 'login' ? '登录' : '注册'}
          </Button>
        </Form>
      </Card>
    </div>
  );
}
