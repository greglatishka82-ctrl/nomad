const API = '/api/admin';

// --- Toast ---
function showToast(msg, type = 'success') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = `toast ${type}`;
    t.classList.remove('hidden');
    setTimeout(() => t.classList.add('hidden'), 3000);
}

// --- Auth ---
document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('login-error');
    errEl.classList.add('hidden');
    try {
        const res = await fetch(`${API}/login`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                username: document.getElementById('username').value,
                password: document.getElementById('password').value,
            }),
            credentials: 'include',
        });
        if (!res.ok) { errEl.textContent = 'Неверный логин или пароль'; errEl.classList.remove('hidden'); return; }
        showDashboard();
    } catch { errEl.textContent = 'Ошибка соединения'; errEl.classList.remove('hidden'); }
});

(async function checkSession() {
    try {
        const res = await fetch(`${API}/faq`, { credentials: 'include' });
        if (res.ok) { showDashboard(); return; }
    } catch {}
    document.getElementById('login-screen').classList.add('active');
})();

function showDashboard() {
    document.getElementById('login-screen').classList.remove('active');
    document.getElementById('dashboard-screen').classList.add('active');
    loadDashboard();
}

// --- Navigation ---
const pageTitles = {
    dashboard: 'Дашборд', bookings: 'Записи', instructors: 'Инструкторы',
    analytics: 'Аналитика', packages: 'Пакеты и сертификаты',
    faq: 'FAQ', notifications: 'Уведомления', audit: 'Логи', settings: 'Настройки',
};

function navigateTo(page) {
    document.querySelectorAll('.nav-item[data-page]').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`.nav-item[data-page="${page}"]`);
    if (btn) btn.classList.add('active');
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${page}`).classList.add('active');
    document.getElementById('page-title').textContent = pageTitles[page] || page;
    // Close mobile sidebar
    document.getElementById('sidebar').classList.remove('open');
    // Load data
    const loaders = { dashboard: loadDashboard, bookings: loadBookings, instructors: loadInstructors, analytics: loadAnalytics, packages: loadPackages, faq: loadFaq, notifications: loadNotifications, audit: loadAudit };
    if (loaders[page]) loaders[page]();
}

document.querySelectorAll('.nav-item[data-page]').forEach(btn => {
    btn.addEventListener('click', () => navigateTo(btn.dataset.page));
});

document.getElementById('logout-btn').addEventListener('click', async () => {
    await fetch(`${API}/logout`, {method: 'POST'});
    location.reload();
});

document.getElementById('mobile-menu-btn').addEventListener('click', () => {
    document.getElementById('sidebar').classList.toggle('open');
});

// --- API helpers ---
async function apiGet(path) {
    const res = await fetch(`${API}${path}`, { credentials: 'include' });
    if (res.status === 401) { location.reload(); return null; }
    return res.json();
}
async function apiPost(path, data) {
    const res = await fetch(`${API}${path}`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data), credentials: 'include' });
    if (res.status === 401) { location.reload(); return null; }
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Ошибка'); }
    return res.json();
}
async function apiPut(path, data) {
    const res = await fetch(`${API}${path}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data), credentials: 'include' });
    if (res.status === 401) { location.reload(); return null; }
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Ошибка'); }
    return res.json();
}
async function apiDelete(path) {
    const res = await fetch(`${API}${path}`, { method: 'DELETE', credentials: 'include' });
    if (res.status === 401) { location.reload(); return null; }
    if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Ошибка'); }
    return res.json();
}

// --- Dashboard ---
async function loadDashboard() {
    const data = await apiGet('/dashboard');
    if (!data) return;
    document.getElementById('stats-grid').innerHTML = `
        <div class="stat-card revenue"><div class="stat-label">Выручка сегодня</div><div class="stat-value">${data.revenue_today.toLocaleString()} ₸</div></div>
        <div class="stat-card revenue"><div class="stat-label">Выручка за неделю</div><div class="stat-value">${data.revenue_week.toLocaleString()} ₸</div></div>
        <div class="stat-card revenue"><div class="stat-label">Выручка за месяц</div><div class="stat-value">${data.revenue_month.toLocaleString()} ₸</div></div>
        <div class="stat-card"><div class="stat-label">Всего записей</div><div class="stat-value">${data.total_bookings}</div></div>
        <div class="stat-card danger"><div class="stat-label">Отменено</div><div class="stat-value">${data.cancelled}</div></div>
        <div class="stat-card danger"><div class="stat-label">Не явились</div><div class="stat-value">${data.no_shows}</div></div>
        <div class="stat-card"><div class="stat-label">Клиентов</div><div class="stat-value">${data.clients_count}</div></div>
        <div class="stat-card"><div class="stat-label">Активных инструкторов</div><div class="stat-value">${data.instructors_count}</div></div>
    `;
}

// --- Bookings ---
const statusLabels = { planned:'Запланирована', confirmed:'Подтверждена', completed:'Завершена', cancelled:'Отменена', no_show:'Не явился' };
const statusBadge = (s) => `<span class="badge badge-${s}">${statusLabels[s]||s}</span>`;

async function loadBookings() {
    const params = new URLSearchParams();
    const df = document.getElementById('filter-date-from').value;
    const dt = document.getElementById('filter-date-to').value;
    const st = document.getElementById('filter-status').value;
    const loc = document.getElementById('filter-location').value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    if (st) params.set('status', st);
    if (loc) params.set('location', loc);
    const data = await apiGet(`/bookings?${params}`);
    if (!data) return;
    const tbody = document.querySelector('#bookings-table tbody');
    if (!data.length) { tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:var(--text-secondary);padding:32px">Нет записей</td></tr>'; return; }
    tbody.innerHTML = data.map(b => `<tr>
        <td>${b.date}</td>
        <td>${b.start_time.slice(0,5)}</td>
        <td><strong>${b.client_name}</strong><br><small style="color:var(--text-secondary)">${b.client_phone||''}</small></td>
        <td>${b.instructor_name}</td>
        <td>${b.service_type==='training'?'Обучение':'Экзамен'}</td>
        <td><small>${b.location}</small></td>
        <td>${statusBadge(b.status)}</td>
        <td><strong>${b.price.toLocaleString()} ₸</strong></td>
        <td>
            <button class="btn btn-danger btn-sm" onclick="deleteBooking(${b.id})">Отменить</button>
        </td>
    </tr>`).join('');
}

async function deleteBooking(id) {
    if (!confirm('Отменить эту запись?')) return;
    await apiDelete(`/bookings/${id}`);
    showToast('Запись отменена');
    loadBookings();
}

function exportBookings() {
    const params = new URLSearchParams();
    const df = document.getElementById('filter-date-from')?.value;
    const dt = document.getElementById('filter-date-to')?.value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    window.open(`${API}/export/bookings?${params}`, '_blank');
}

function exportClients() { window.open(`${API}/export/clients`, '_blank'); }

// --- Instructors ---
let editingInstructorId = null;

async function loadInstructors() {
    const data = await apiGet('/instructors');
    if (!data) return;
    const transLabels = {manual:'Механика',automatic:'Автомат',both:'Механика и автомат'};
    const list = document.getElementById('instructors-list');
    if (!data.length) { list.innerHTML = '<p style="color:var(--text-secondary)">Нет инструкторов</p>'; return; }
    list.innerHTML = data.map(i => `<div class="inst-card">
        <div class="inst-info">
            <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
                ${i.avatar_url ? `<img src="${i.avatar_url}" style="width:48px;height:48px;border-radius:50%;object-fit:cover;" alt="${i.name}">` : `<div style="width:48px;height:48px;border-radius:50%;background:var(--primary);color:white;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:18px;">${i.name.charAt(0)}</div>`}
                <div>
                    <h3 style="margin:0;">${i.name}</h3>
                    <p style="margin:0;">${transLabels[i.transmission]} · Стаж ${i.experience_years} лет</p>
                </div>
            </div>
            <p>TG: ${i.telegram_username ? '@'+i.telegram_username : (i.telegram_id || '—')}</p>
            <p>${i.working_hours_start?.slice(0,5)||'09:00'}–${i.working_hours_end?.slice(0,5)||'19:00'}</p>
            ${i.description ? `<p style="color:var(--text-secondary);margin-top:8px;font-style:italic">«${i.description}»</p>` : ''}
            <div class="inst-actions">
                <button class="btn btn-outline btn-sm" onclick="editInstructor(${i.id})">✏️ Редактировать</button>
                <button class="btn btn-danger btn-sm" onclick="deleteInstructor(${i.id})">Удалить</button>
            </div>
        </div>
        <div class="inst-rating">⭐ ${i.rating.toFixed(1)}</div>
    </div>`).join('');
}

function showInstructorForm() {
    editingInstructorId = null;
    document.getElementById('instructor-form-title').textContent = 'Новый инструктор';
    document.getElementById('inst-name').value = '';
    document.getElementById('inst-tg-id').value = '';
    document.getElementById('inst-tg-user').value = '';
    document.getElementById('inst-trans').value = 'both';
    document.getElementById('inst-exp').value = '0';
    document.getElementById('inst-start').value = '09:00';
    document.getElementById('inst-end').value = '19:00';
    document.getElementById('inst-lunch-start').value = '';
    document.getElementById('inst-lunch-end').value = '';
    document.getElementById('inst-desc').value = '';
    document.getElementById('inst-avatar').value = '';
    document.getElementById('inst-avatar-preview').innerHTML = '';
    document.querySelectorAll('.day-off-cb').forEach(cb => {
        cb.checked = cb.value === 'Суббота' || cb.value === 'Воскресенье';
    });
    document.getElementById('instructor-form').classList.remove('hidden');
}

function hideInstructorForm() {
    document.getElementById('instructor-form').classList.add('hidden');
    editingInstructorId = null;
}

async function editInstructor(id) {
    const data = await apiGet('/instructors');
    if (!data) return;
    const i = data.find(x => x.id === id);
    if (!i) return;
    editingInstructorId = id;
    document.getElementById('instructor-form-title').textContent = 'Редактировать инструктора';
    document.getElementById('inst-name').value = i.name;
    document.getElementById('inst-tg-id').value = i.telegram_id || '';
    document.getElementById('inst-tg-user').value = i.telegram_username || '';
    document.getElementById('inst-trans').value = i.transmission;
    document.getElementById('inst-exp').value = i.experience_years;
    document.getElementById('inst-start').value = (i.working_hours_start || '09:00').slice(0,5);
    document.getElementById('inst-end').value = (i.working_hours_end || '19:00').slice(0,5);
    document.getElementById('inst-lunch-start').value = (i.lunch_start || '').slice(0,5);
    document.getElementById('inst-lunch-end').value = (i.lunch_end || '').slice(0,5);
    document.getElementById('inst-desc').value = i.description || '';
    const daysOff = (i.days_off || '').split(',').map(d => d.trim());
    document.querySelectorAll('.day-off-cb').forEach(cb => {
        cb.checked = daysOff.includes(cb.value);
    });
    // Показываем текущую аватарку
    const preview = document.getElementById('inst-avatar-preview');
    if (i.avatar_url) {
        preview.innerHTML = `<img src="${i.avatar_url}" style="max-width:100px;max-height:100px;border-radius:8px;" alt="Аватар">`;
    } else {
        preview.innerHTML = '';
    }
    document.getElementById('inst-avatar').value = '';
    document.getElementById('instructor-form').classList.remove('hidden');
}

async function saveInstructor() {
    const checkedDays = Array.from(document.querySelectorAll('.day-off-cb:checked')).map(cb => cb.value);
    const lunchStart = document.getElementById('inst-lunch-start').value || null;
    const lunchEnd = document.getElementById('inst-lunch-end').value || null;
    const payload = {
        name: document.getElementById('inst-name').value,
        telegram_id: document.getElementById('inst-tg-id').value || null,
        telegram_username: document.getElementById('inst-tg-user').value || null,
        transmission: document.getElementById('inst-trans').value,
        experience_years: parseInt(document.getElementById('inst-exp').value) || 0,
        working_hours_start: document.getElementById('inst-start').value,
        working_hours_end: document.getElementById('inst-end').value,
        lunch_start: lunchStart,
        lunch_end: lunchEnd,
        days_off: checkedDays.join(','),
        description: document.getElementById('inst-desc').value || null,
    };
    let instId = editingInstructorId;
    if (instId) {
        await apiPut(`/instructors/${instId}`, payload);
        showToast('Инструктор обновлён');
    } else {
        const result = await apiPost('/instructors', payload);
        instId = result.id;
        showToast('Инструктор добавлен');
    }
    // Загружаем аватарку если выбрана
    if (instId) {
        await uploadInstructorAvatar(instId);
    }
    hideInstructorForm();
    loadInstructors();
}

async function deleteInstructor(id) {
    if (!confirm('Удалить этого инструктора?')) return;
    await apiDelete(`/instructors/${id}`);
    showToast('Инструктор удалён');
    loadInstructors();
}

// --- Analytics ---
async function loadAnalytics() {
    const [heatmap, load] = await Promise.all([apiGet('/analytics/heatmap'), apiGet('/analytics/instructor-load')]);
    // Heatmap
    const hc = document.getElementById('heatmap-container');
    if (heatmap && heatmap.length) {
        const maxCount = Math.max(...heatmap.map(h => h.count), 1);
        const dayNames = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
        let html = '';
        // Group by date
        const byDate = {};
        heatmap.forEach(h => { if (!byDate[h.date]) byDate[h.date] = []; byDate[h.date].push(h); });
        Object.entries(byDate).slice(-7).forEach(([d, hours]) => {
            const dayName = hours[0]?.day_name || d;
            html += `<div style="text-align:center;font-size:11px;font-weight:600;margin-bottom:4px;color:var(--text-secondary)">${dayName}</div>`;
            for (let h = 9; h < 18; h++) {
                const entry = hours.find(x => x.hour === h);
                const count = entry ? entry.count : 0;
                const intensity = count / maxCount;
                const bg = count === 0 ? '#f1f5f9' : `rgba(37,99,235,${0.2 + intensity * 0.8})`;
                html += `<div class="heatmap-cell" style="background:${bg}" title="${h}:00 — ${count} записей">${count||''}</div>`;
            }
        });
        hc.innerHTML = html;
    } else { hc.innerHTML = '<p style="color:var(--text-secondary)">Нет данных</p>'; }

    // Instructor load
    const lc = document.getElementById('instructor-load-chart');
    if (load && load.length) {
        const maxB = Math.max(...load.map(l => l.bookings), 1);
        lc.innerHTML = load.map(l => `<div class="load-bar-container">
            <div class="load-bar-label"><span>${l.name}</span><span>${l.bookings} записей</span></div>
            <div class="load-bar"><div class="load-bar-fill" style="width:${(l.bookings/maxB*100).toFixed(1)}%">${l.bookings}</div></div>
        </div>`).join('');
    } else { lc.innerHTML = '<p style="color:var(--text-secondary)">Нет данных за последние 30 дней</p>'; }
}

// --- Packages & Certificates ---
async function loadPackages() {
    const data = await apiGet('/packages');
    if (!data) return;
    const list = document.getElementById('packages-list');
    if (!data.length) { list.innerHTML = '<p style="color:var(--text-secondary)">Нет пакетов</p>'; return; }
    list.innerHTML = data.map(p => `<div class="inst-card">
        <div class="inst-info">
            <h3>${p.name}</h3>
            <p>${p.sessions_count} занятий · ${p.price.toLocaleString()} ₸</p>
        </div>
        <button class="btn btn-danger btn-sm" onclick="deletePackage(${p.id})">Удалить</button>
    </div>`).join('');
    loadCertificates();
}

async function createPackage() {
    const name = document.getElementById('pkg-name').value;
    const sessions = parseInt(document.getElementById('pkg-sessions').value);
    const price = parseInt(document.getElementById('pkg-price').value);
    if (!name || !sessions || !price) { showToast('Заполните все поля', 'error'); return; }
    try {
        await apiPost('/packages', {name, sessions_count: sessions, price});
        showToast('Пакет создан');
        document.getElementById('pkg-name').value = '';
        document.getElementById('pkg-sessions').value = '';
        document.getElementById('pkg-price').value = '';
        loadPackages();
    } catch (e) { showToast(e.message, 'error'); }
}

async function deletePackage(id) {
    if (!confirm('Удалить пакет?')) return;
    try {
        await apiDelete(`/packages/${id}`);
        showToast('Пакет удалён');
        loadPackages();
    } catch (e) { showToast(e.message, 'error'); }
}

async function loadCertificates() {
    const data = await apiGet('/certificates');
    if (!data) return;
    const list = document.getElementById('certificates-list');
    if (!data.length) { list.innerHTML = '<p style="color:var(--text-secondary)">Нет сертификатов</p>'; return; }
    list.innerHTML = data.map(c => `<div class="inst-card">
        <div class="inst-info">
            <h3>${c.code}</h3>
            <p>${c.nominal.toLocaleString()} ₸ · Остаток: ${c.remaining.toLocaleString()} ₸ · ${c.is_used ? 'Использован' : 'Активен'}${c.client_name ? ' · Клиент: ' + c.client_name : ''}</p>
        </div>
        <button class="btn btn-danger btn-sm" onclick="deleteCertificate(${c.id})">Удалить</button>
    </div>`).join('');
}

async function createCertificate() {
    const nominal = parseInt(document.getElementById('cert-nominal').value);
    if (!nominal) { showToast('Укажите номинал', 'error'); return; }
    try {
        const data = await apiPost('/certificates', {nominal});
        document.getElementById('cert-result').innerHTML = `<div class="alert alert-success">Код: <strong>${data.code}</strong> · Номинал: ${data.nominal} ₸</div>`;
        document.getElementById('cert-nominal').value = '';
        loadCertificates();
    } catch (e) { showToast(e.message, 'error'); }
}

async function deleteCertificate(id) {
    if (!confirm('Удалить сертификат?')) return;
    try {
        await apiDelete(`/certificates/${id}`);
        showToast('Сертификат удалён');
        loadCertificates();
    } catch (e) { showToast(e.message, 'error'); }
}

// --- FAQ ---
async function loadFaq() {
    const data = await apiGet('/faq');
    if (!data) return;
    const list = document.getElementById('faq-list');
    if (!data.length) { list.innerHTML = '<p style="color:var(--text-secondary)">Нет вопросов</p>'; return; }
    list.innerHTML = data.map(f => `<div class="faq-admin-item">
        <div><h4>${f.question}</h4><p>${f.answer}</p></div>
        <div>
            <button class="btn btn-sm" onclick="editFaq(${f.id}, '${f.question.replace(/'/g, "\\'")}', '${f.answer.replace(/'/g, "\\'")}')">Редактировать</button>
            <button class="btn btn-danger btn-sm" onclick="deleteFaq(${f.id})">Удалить</button>
        </div>
    </div>`).join('');
}

async function createFaq() {
    const q = document.getElementById('faq-q').value;
    const a = document.getElementById('faq-a').value;
    if (!q || !a) { showToast('Заполните вопрос и ответ', 'error'); return; }
    await apiPost('/faq', {question: q, answer: a});
    showToast('Вопрос добавлен');
    document.getElementById('faq-q').value = '';
    document.getElementById('faq-a').value = '';
    loadFaq();
}

async function deleteFaq(id) {
    if (!confirm('Удалить этот вопрос?')) return;
    await apiDelete(`/faq/${id}`);
    showToast('Вопрос удалён');
    loadFaq();
}

function editFaq(id, question, answer) {
    document.getElementById('faq-q').value = question;
    document.getElementById('faq-a').value = answer;
    document.getElementById('faq-q').focus();
    const form = document.getElementById('faq-form');
    form.onsubmit = async function(e) {
        e.preventDefault();
        const q = document.getElementById('faq-q').value;
        const a = document.getElementById('faq-a').value;
        if (!q || !a) { showToast('Заполните вопрос и ответ', 'error'); return; }
        await apiPut(`/faq/${id}`, {question: q, answer: a});
        showToast('Вопрос обновлён');
        document.getElementById('faq-q').value = '';
        document.getElementById('faq-a').value = '';
        form.onsubmit = null;
        loadFaq();
    };
}

// --- Notifications ---
async function loadNotifications() {
    const data = await apiGet('/notifications');
    if (!data) return;
    const list = document.getElementById('notifications-list');
    if (!data.length) { list.innerHTML = '<p style="color:var(--text-secondary)">Нет уведомлений</p>'; return; }
    const icons = {
        low_rating: '⚠️', new_booking: '📋', new_client: '👤', booking_confirmed: '✅',
        booking_cancelled: '❌', no_show: '🚫', rating_given: '⭐', confirmation_sent: '📨',
        client_arrived: '🚶', lesson_completed: '💰', create_instructor: '👨‍🏫',
        update_instructor: '✏️', delete_instructor: '🗑️', delete_booking: '🗑️',
        reassign_booking: '🔄', create_faq: '❓', update_faq: '✏️', delete_faq: '🗑️',
        create_package: '📦', delete_package: '🗑️', create_certificate: '🎟️',
        delete_certificate: '🗑️', change_password: '🔑', rating_request_sent: '📊',
        mobile_registration: '📱', mobile_booking_planned: '📋', mobile_booking_confirmed: '✅',
        mobile_booking_in_progress: '🚗', mobile_booking_completed: '🎉', mobile_booking_cancelled: '❌',
        mobile_booking_no_show: '🚫', support_message: '💬',
    };
    list.innerHTML = data.map(n => `<div class="notif-item notif-${n.type}">
        <div class="notif-icon">${icons[n.type]||'🔔'}</div>
        <div class="notif-body">
            <h4>${n.message}</h4>
            <p>${new Date(n.created_at).toLocaleString('ru-RU')}</p>
        </div>
    </div>`).join('');
}

// --- Audit ---
async function loadAudit() {
    const data = await apiGet('/audit-logs');
    if (!data) return;
    const tbody = document.querySelector('#audit-table tbody');
    if (!data.length) { tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-secondary);padding:32px">Нет записей</td></tr>'; return; }
    const icons = {
        new_client: '👤', new_booking: '📋', booking_confirmed: '✅', booking_cancelled: '❌',
        no_show: '🚫', rating_given: '⭐', rating_request_sent: '📊', confirmation_sent: '📨',
        low_rating: '⚠️', create_instructor: '👨‍🏫', update_instructor: '✏️', delete_instructor: '🗑️',
        delete_booking: '🗑️', reassign_booking: '🔄', create_faq: '❓', update_faq: '✏️',
        delete_faq: '🗑️', create_package: '📦', delete_package: '🗑️',
        create_certificate: '🎟️', delete_certificate: '🗑️', change_password: '🔑',
        client_arrived: '🚶', lesson_completed: '💰',
    };
    const labels = {
        new_client: 'Новый клиент зарегистрировался',
        new_booking: 'Новая запись на занятие',
        booking_confirmed: 'Клиент подтвердил запись',
        booking_cancelled: 'Клиент отменил запись',
        no_show: 'Неявка — клиент не пришёл',
        rating_given: 'Клиент оценил инструктора',
        rating_request_sent: 'Запрос оценки отправлен клиенту',
        confirmation_sent: 'Напоминание о подтверждении отправлено',
        low_rating: 'Низкий рейтинг инструктора',
        create_instructor: 'Добавлен новый инструктор',
        update_instructor: 'Данные инструктора обновлены',
        delete_instructor: 'Инструктор удалён',
        delete_booking: 'Запись удалена администратором',
        reassign_booking: 'Запись переназначена',
        create_faq: 'Добавлен новый вопрос в FAQ',
        update_faq: 'Вопрос в FAQ обновлён',
        delete_faq: 'Вопрос удалён из FAQ',
        create_package: 'Добавлен новый пакет занятий',
        delete_package: 'Пакет занятий удалён',
        create_certificate: 'Создан подарочный сертификат',
        delete_certificate: 'Сертификат удалён',
        change_password: 'Администратор сменил пароль',
    };
    tbody.innerHTML = data.map(l => {
        const icon = icons[l.action] || '📝';
        const label = labels[l.action] || l.action;
        return `<tr>
            <td>${new Date(l.created_at).toLocaleString('ru-RU')}</td>
            <td><strong>${icon} ${label}</strong></td>
            <td>${l.admin_username === 'bot' ? '🤖 Бот' : '👤 ' + l.admin_username}</td>
            <td style="color:var(--text-secondary)">${l.details||'—'}</td>
        </tr>`;
    }).join('');
}

// --- Settings ---
async function changePassword() {
    const oldP = document.getElementById('old-pass').value;
    const newP = document.getElementById('new-pass').value;
    if (!oldP || !newP) { showToast('Заполните оба поля', 'error'); return; }
    const res = await apiPost('/change-password', {old_password: oldP, new_password: newP});
    if (res.ok) {
        showToast('Пароль изменён');
        document.getElementById('old-pass').value = '';
        document.getElementById('new-pass').value = '';
    } else {
        showToast('Ошибка: неверный текущий пароль', 'error');
    }
}


// ═══════════════════════════════════════════════════════════════════
// SUPPORT CHAT
// ═══════════════════════════════════════════════════════════════════

pageTitles.support = 'Поддержка клиентов';
let currentDialog = null;

async function loadSupport() {
    const dialogs = await apiGet('/support/dialogs');
    if (!dialogs) return;
    allDialogs = dialogs;
    renderDialogs(dialogs);
}

async function openDialog(userId) {
    currentDialog = userId;

    // Подсветка активного диалога
    document.querySelectorAll('.dialog-item').forEach(el => el.classList.remove('active'));
    document.querySelector(`.dialog-item[data-user="${userId}"]`)?.classList.add('active');

    const data = await apiGet(`/support/dialogs/${userId}`);
    if (!data) return;

    document.getElementById('chat-empty').classList.add('hidden');
    document.getElementById('chat-active').classList.remove('hidden');

    document.getElementById('chat-user-name').textContent = data.user.name;
    document.getElementById('chat-user-contacts').textContent = `${data.user.phone} • ${data.user.email}`;

    const msgs = document.getElementById('chat-messages');
    msgs.innerHTML = '';
    data.messages.forEach(msg => {
        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${msg.sender}`;
        bubble.innerHTML = `
            <div>${escapeHtml(msg.text)}</div>
            <div class="message-time">${formatDateTime(msg.created_at)}</div>
        `;
        msgs.appendChild(bubble);
    });

    // Скролл вниз
    msgs.scrollTop = msgs.scrollHeight;

    // Убираем метку "новое"
    const dialogItem = Array.from(document.querySelectorAll('.dialog-item')).find(el => {
        return el.querySelector('.dialog-user-name').textContent === data.user.name;
    });
    if (dialogItem) {
        dialogItem.classList.remove('has-new');
    }
}

async function sendReply() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;

    try {
        const msg = await apiPost(`/support/dialogs/${currentDialog}/reply`, { text });
        
        // Добавить сообщение в чат
        const msgs = document.getElementById('chat-messages');
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble admin';
        bubble.innerHTML = `
            <div>${escapeHtml(msg.text)}</div>
            <div class="message-time">${formatDateTime(msg.created_at)}</div>
        `;
        msgs.appendChild(bubble);
        msgs.scrollTop = msgs.scrollHeight;

        input.value = '';
        showToast('Ответ отправлен');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function showClientBookings() {
    if (!currentDialog) return;
    // Переключаемся на страницу записей и фильтруем по клиенту
    navigateTo('bookings');
    showToast('Показываются записи клиента', 'info');
}

// Обработчик для Enter в textarea
document.addEventListener('DOMContentLoaded', () => {
    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
        chatInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendReply();
            }
        });
    }
});

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatDateTime(isoString) {
    if (!isoString) return '';
    const d = new Date(isoString);
    const today = new Date();
    const isToday = d.toDateString() === today.toDateString();
    const time = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    if (isToday) return `Сегодня в ${time}`;
    return `${d.toLocaleDateString('ru-RU')} в ${time}`;
}

// ═══════════════════════════════════════════════════════════════════
// SUPPORT SEARCH
// ═══════════════════════════════════════════════════════════════════

let allDialogs = [];

function filterDialogs() {
    const query = document.getElementById('support-search').value.toLowerCase();
    const container = document.getElementById('dialogs-container');
    const filtered = allDialogs.filter(d =>
        d.user_name.toLowerCase().includes(query) ||
        d.user_phone.toLowerCase().includes(query) ||
        d.user_email.toLowerCase().includes(query)
    );
    renderDialogs(filtered);
}

function renderDialogs(dialogs) {
    const container = document.getElementById('dialogs-container');
    container.innerHTML = '';
    dialogs.forEach(d => {
        const item = document.createElement('div');
        item.className = `dialog-item${d.has_new ? ' has-new' : ''}`;
        item.setAttribute('data-user', d.user_id);
        item.innerHTML = `
            <div class="dialog-user-name">${escapeHtml(d.user_name)}</div>
            <div class="dialog-contact">${escapeHtml(d.user_phone)} • ${escapeHtml(d.user_email)}</div>
            <div class="dialog-last-msg">${escapeHtml(d.last_message)}</div>
            <div class="dialog-time">${formatDateTime(d.last_message_at)}</div>
        `;
        item.onclick = () => openDialog(d.user_id);
        container.appendChild(item);
    });
}

// ═══════════════════════════════════════════════════════════════════
// INSTRUCTOR AVATAR UPLOAD
// ═══════════════════════════════════════════════════════════════════

async function uploadInstructorAvatar(instructorId) {
    const fileInput = document.getElementById('inst-avatar');
    if (!fileInput.files.length) return;
    const file = fileInput.files[0];
    if (file.size > 2 * 1024 * 1024) {
        showToast('Максимальный размер — 2 МБ', 'error');
        return;
    }
    const formData = new FormData();
    formData.append('file', file);
    try {
        const res = await fetch(`${API}/instructors/${instructorId}/avatar`, {
            method: 'POST',
            body: formData,
            credentials: 'include',
        });
        if (!res.ok) {
            const e = await res.json().catch(() => ({}));
            throw new Error(e.detail || 'Ошибка загрузки');
        }
        showToast('Аватарка загружена');
        fileInput.value = '';
        document.getElementById('inst-avatar-preview').innerHTML = '';
        loadInstructors();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// Добавить загрузчик в объект loaders
if (typeof loaders !== 'undefined') {
    loaders.support = loadSupport;
} else {
    // Если loaders ещё не определён, добавим обработчик после загрузки
    document.addEventListener('DOMContentLoaded', () => {
        const originalNavigateTo = window.navigateTo;
        window.navigateTo = function(page) {
            originalNavigateTo(page);
            if (page === 'support') loadSupport();
        };
    });
}
