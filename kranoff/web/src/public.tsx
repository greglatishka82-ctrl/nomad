import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, type Bootstrap } from './api';
import { KZ_MAP } from './i18n-kk';

type Lang = 'ru' | 'kk' | 'en';

type PublicContextValue = {
  data: Bootstrap | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (ru: string, en: string) => string;
  text3: (ru: string, kk: string, en: string) => string;
};

const PublicContext = createContext<PublicContextValue | null>(null);

export function PublicProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Bootstrap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lang, setLangState] = useState<Lang>(() => {
    try {
      const saved = localStorage.getItem('kranoff_lang');
      return saved === 'en' || saved === 'kk' ? saved : 'ru';
    } catch {
      return 'ru';
    }
  });

  const load = () => {
    setLoading(true);
    setError(null);
    api<Bootstrap>('/api/public/bootstrap')
      .then((result) => setData(result))
      .catch((e) => setError(e instanceof Error ? e.message : 'Не удалось загрузить данные'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
    try {
      localStorage.setItem('kranoff_lang', lang);
    } catch {
      // ignore
    }
  }, [lang]);

  const value = useMemo<PublicContextValue>(
    () => ({
      data,
      loading,
      error,
      reload: load,
      lang,
      setLang: setLangState,
      t: (ru: string, en: string) => (lang === 'kk' ? KZ_MAP[ru] || ru : lang === 'en' ? en : ru),
      text3: (ru: string, kk: string, en: string) => (lang === 'kk' ? kk || ru : lang === 'en' ? en || ru : ru),
    }),
    [data, loading, error, lang],
  );

  return <PublicContext.Provider value={value}>{children}</PublicContext.Provider>;
}

export function usePublic(): PublicContextValue {
  const ctx = useContext(PublicContext);
  if (!ctx) throw new Error('usePublic must be used inside PublicProvider');
  return ctx;
}

export function useDocumentMeta(title: string, description?: string) {
  useEffect(() => {
    document.title = title;
    if (description) {
      const meta = document.querySelector('meta[name="description"]');
      if (meta) meta.setAttribute('content', description);
    }
  }, [title, description]);
}

export function telegramUrl(username: string, start?: string): string {
  const clean = (username || 'kranof_bot').replace(/^@/, '');
  return `https://t.me/${clean}${start ? `?start=${encodeURIComponent(start)}` : ''}`;
}

export function whatsappUrl(phone: string): string {
  return `https://wa.me/${String(phone || '').replace(/\D/g, '')}`;
}

export function telUrl(phone: string): string {
  return `tel:${String(phone || '').replace(/[^\d+]/g, '')}`;
}
