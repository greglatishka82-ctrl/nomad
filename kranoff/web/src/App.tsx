import { Navigate, Route, Routes } from 'react-router-dom';
import { PublicProvider } from './public';
import { SiteLayout } from './site/SiteLayout';
import { HomePage } from './site/HomePage';
import { ServicesPage } from './site/ServicesPage';
import { AboutPage } from './site/AboutPage';
import { ContactsPage } from './site/ContactsPage';
import { PrivacyPage } from './site/PrivacyPage';
import { AdminApp } from './admin/AdminApp';
import { MasterPage } from './master/MasterPage';

export function App() {
  return (
    <Routes>
      <Route path="/admin/*" element={<AdminApp />} />
      <Route path="/m/:token" element={<MasterPage />} />
      <Route
        element={
          <PublicProvider>
            <SiteLayout />
          </PublicProvider>
        }
      >
        <Route path="/" element={<HomePage />} />
        <Route path="/services" element={<ServicesPage />} />
        <Route path="/about" element={<AboutPage />} />
        <Route path="/contacts" element={<ContactsPage />} />
        <Route path="/privacy" element={<PrivacyPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
