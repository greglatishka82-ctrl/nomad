import { usePublic, useDocumentMeta } from '../public';

export function PrivacyPage() {
  const { data, t, text3, lang } = usePublic();
  useDocumentMeta(lang === 'kk' ? 'Құпиялылық саясаты — KRANOFF' : lang === 'en' ? 'Privacy policy — KRANOFF' : 'Политика конфиденциальности — KRANOFF');
  if (!data) return null;
  const company = data.company;
  const text = text3(company.privacy_ru, company.privacy_kz, company.privacy_en);
  const paragraphs = text.split(/\n{2,}/).filter(Boolean);
  return (
    <section className="section">
      <div className="container" style={{ maxWidth: 860 }}>
        <span className="eyebrow">{t('Документы', 'Documents')}</span>
        <h1 className="h2" style={{ marginTop: 10 }}>{t('Политика конфиденциальности', 'Privacy policy')}</h1>
        <p className="lead">{t('Как мы обращаемся с данными клиентов.', 'How we handle client data.')}</p>
        <div className="panel mt-20" style={{ padding: 26 }}>
          {paragraphs.map((p, index) => (
            <p key={index} className="pre" style={{ marginBottom: 14 }}>{p}</p>
          ))}
          <div className="divider" />
          <p className="pre">
            {lang === 'kk'
              ? `Деректерді өңдеу бойынша сұрақтарыңыз болса немесе деректеріңізді жойғыңыз келсе — Telegram @${company.telegram} арқылы жазыңыз немесе ${company.phone || 'қызмет'} телефонына қоңырау шалыңыз.`
              : lang === 'en'
                ? `If you have questions about data processing or want your data deleted — message @${company.telegram} on Telegram or call ${company.phone || 'the service'}.`
                : `Если у вас есть вопросы по обработке данных или вы хотите удалить свои данные — напишите в Telegram @${company.telegram} или позвоните по телефону ${company.phone || 'службы'}.`}
          </p>
        </div>
      </div>
    </section>
  );
}
