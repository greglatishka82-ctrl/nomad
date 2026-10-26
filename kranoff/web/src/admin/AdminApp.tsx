import { useEffect, useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { api, type AdminUser, type Shift } from '../api';
import { ToastProvider } from '../ui';
import { Login } from './Login';
import { AdminLayout } from './AdminLayout';
import { DashboardPage } from './DashboardPage';
import { OrdersPage } from './OrdersPage';
import { OrderDetailPage } from './OrderDetailPage';
import { MapPage } from './MapPage';
import { ClientsPage } from './ClientsPage';
import { MastersPage } from './MastersPage';
import { ServicesPage } from './ServicesPage';
import { SupportPage } from './SupportPage';
import { AnalyticsPage } from './AnalyticsPage';
import { AdminsPage } from './AdminsPage';
import { LogsPage } from './LogsPage';
import { SettingsPage } from './SettingsPage';

export type Me = {
  admin: AdminUser;
  shift: Shift | null;
  onShift: Array<AdminUser & { shift_id: number; shift_started_at: string }>;
};

export function AdminApp() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    api<Me>('/api/auth/me')
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const handler = () => load();
    window.addEventListener('kranoff:auth', handler);
    return () => window.removeEventListener('kranoff:auth', handler);
  }, []);

  if (loading) {
    return (
      <div className="login">
        <div className="loading" style={{ color: '#fff' }}>
          <span className="spinner" /> Загрузка…
        </div>
      </div>
    );
  }

  if (!me) {
    return <Login onSuccess={load} />;
  }

  return (
    <ToastProvider>
      <AdminLayout me={me} onRefresh={load} onLogout={() => setMe(null)}>
        <Routes>
          <Route index element={<DashboardPage />} />
          <Route path="orders" element={<OrdersPage />} />
          <Route path="orders/:id" element={<OrderDetailPage />} />
          <Route path="map" element={<MapPage />} />
          <Route path="clients" element={<ClientsPage />} />
          <Route path="masters" element={<MastersPage />} />
          <Route path="services" element={<ServicesPage />} />
          <Route path="support" element={<SupportPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="admins" element={<AdminsPage />} />
          <Route path="logs" element={<LogsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Routes>
      </AdminLayout>
    </ToastProvider>
  );
}
