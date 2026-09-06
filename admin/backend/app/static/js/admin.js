// API URL из переменной окружения Vite
const API = '/api/admin';

// --- Global state ---
let _badgePollInterval = null;
let _prevCounts = { new_clients: 0, unread_support: 0, unread_bookings: 0, pending_applications_count: 0, unread_notifications: 0, conflicts_count: 0 };
let _badgeViewVersion = 0;
let _notificationCountsInitialized = false;
let _notificationPollSequence = 0;
let _applicationAlertPlayback = null;
let _applicationAlertPlaybackId = 0;
const APPLICATION_SOUND_STORAGE_KEY = 'nomad-admin-application-sound-enabled';
let applicationSoundEnabled = (() => {
    try {
        return localStorage.getItem(APPLICATION_SOUND_STORAGE_KEY) !== 'off';
    } catch {
        return true;
    }
})();
let supportPollInterval = null;
let lastSupportUnread = 0;
let isAdminOffline = !navigator.onLine;
let offlineHeartbeatTimer = null;
let offlineHeartbeatInFlight = false;
let offlineReplayInProgress = false;
let offlineReplayPromise = null;
let lastOfflineIssueSignature = '';
const adminMutationInFlight = new Map();
const applicationAlertAudio = new Audio('/static/sounds/notification.mp3');
applicationAlertAudio.preload = 'auto';
applicationAlertAudio.volume = 0.85;

// IndexedDB keeps both the last usable view and a durable write queue.  We do
// not use localStorage for records: it is too small and can be cleared by the
// browser without an explicit transaction.
const offlineDb = new Promise((resolve, reject) => {
    const request = indexedDB.open('nomad-admin-offline', 1);
    request.onupgradeneeded = () => {
        const db = request.result;
        db.createObjectStore('api-cache');
        db.createObjectStore('operations', { keyPath: 'id' });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
});

async function offlineStore(store, key, value) {
    const db = await offlineDb;
    return new Promise((resolve, reject) => {
        const tx = db.transaction(store, 'readwrite');
        // `operations` has an inline keyPath (`id`), whereas response cache
        // uses explicit keys. Passing a key to the former causes DataError and
        // was preventing every offline write from entering the queue.
        const objectStore = tx.objectStore(store);
        if (objectStore.keyPath) objectStore.put(value);
        else objectStore.put(value, key);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
    });
}
async function offlineRead(store, key) {
    const db = await offlineDb;
    return new Promise((resolve, reject) => {
        const tx = db.transaction(store, 'readonly');
        const req = tx.objectStore(store).get(key);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}
async function replaceOfflineApiCache(snapshot) {
    // A snapshot is useful only when all its parts describe the same server
    // state.  Clearing and replacing the cache in one IndexedDB transaction
    // prevents a new booking list from being paired with yesterday's slots.
    const db = await offlineDb;
    const data = snapshot?.data || {};
    const offlineSnapshot = {
        ...snapshot,
        instructors: data['/instructors'] || [],
        bookings: data['/bookings'] || [],
        vehicles: data['/vehicles'] || [],
        clients: data['/clients'] || [],
    };
    return new Promise((resolve, reject) => {
        const tx = db.transaction('api-cache', 'readwrite');
        const store = tx.objectStore('api-cache');
        store.clear();
        Object.entries(data).forEach(([path, value]) => store.put(value, path));
        store.put(offlineSnapshot, 'offline-snapshot');
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
        tx.onabort = () => reject(tx.error || new Error('Не удалось заменить офлайн-снимок'));
    });
}
async function offlineOperations() {
    const db = await offlineDb;
    return new Promise((resolve, reject) => {
        const req = db.transaction('operations', 'readonly').objectStore('operations').getAll();
        req.onsuccess = () => resolve(req.result || []);
        req.onerror = () => reject(req.error);
    });
}
async function removeOfflineOperation(id) {
    const db = await offlineDb;
    return new Promise((resolve, reject) => {
        const tx = db.transaction('operations', 'readwrite');
        tx.objectStore('operations').delete(id);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
    });
}
async function updateOfflineOperation(operation) {
    const db = await offlineDb;
    return new Promise((resolve, reject) => {
        const tx = db.transaction('operations', 'readwrite');
        tx.objectStore('operations').put(operation);
        tx.oncomplete = resolve;
        tx.onerror = () => reject(tx.error);
    });
}
async function persistOfflineIdMappings(idMap = {}, valueMap = {}) {
    const operations = await offlineOperations();
    const remapValue = value => {
        if (Array.isArray(value)) return value.map(remapValue);
        if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, remapValue(item)]));
        if (typeof value === 'number' && idMap[String(value)] !== undefined) return Number(idMap[String(value)]);
        if (typeof value === 'string' && valueMap[value] !== undefined) return valueMap[value];
        return value;
    };
    for (const operation of operations) {
        const path = operation.path.split('/').map(part => idMap[part] !== undefined ? String(idMap[part]) : part).join('/');
        await updateOfflineOperation({ ...operation, path, body: remapValue(operation.body) });
    }
}
function renderOfflineIssues(operations) {
    const errors = (operations || []).filter(op => op.sync_error);
    const indicator = document.getElementById('offline-issues-status');
    if (!indicator) return;
    indicator.classList.toggle('hidden', errors.length === 0);
    if (!errors.length) {
        indicator.textContent = '';
        indicator.title = '';
        return;
    }
    indicator.textContent = `⚠ ${errors.length}: ошибки синхронизации`;
    indicator.title = errors.map(operation => operation.sync_error).join('\n');
}

function openOfflineIssues() {
    navigateTo('bookings');
    switchBookingTab('conflicts');
}
function setOfflineState(offline) {
    isAdminOffline = offline;
    document.getElementById('offline-status')?.classList.toggle('hidden', !offline);
}

async function waitForOfflineReplay() {
    // Only a real reconnect with queued local operations is ordered. The
    // recurring snapshot never sets this promise and stays fully parallel.
    const pendingReplay = offlineReplayPromise;
    if (pendingReplay) await pendingReplay;
}

function isValidClientPassword(password) {
    return /^[!-~]{6,}$/.test(password) && /[A-Za-z]/.test(password);
}

async function validateOfflineOperation(method, path, body) {
    const snapshot = await offlineRead('api-cache', 'offline-snapshot');
    if (!snapshot) throw new Error('Локальная база ещё не создана. Сначала откройте админку при наличии интернета.');
    if (method === 'POST' && path === '/clients') {
        const phone = String(body?.phone || '').replace(/\D/g, '').slice(-10);
        if (snapshot.clients?.some(client => phone && String(client.phone || '').replace(/\D/g, '').slice(-10) === phone)) {
            throw new Error('Клиент с таким телефоном уже существует');
        }
    }
    if (method === 'POST' && path === '/bookings/manual') {
        const window = snapshot.booking_window || {};
        if ((window.min_date && body.booking_date < window.min_date) || (window.max_date && body.booking_date > window.max_date)) {
            throw new Error(`Дата должна быть в диапазоне ${window.min_date} — ${window.max_date}`);
        }
        const slots = await buildOfflineSlots(body.booking_date, body.service_type, body.transmission, body.instructor_id);
        if (!slots.slots.some(slot => slot.time === body.start_time && slot.is_free)) {
            throw new Error('Выбранный слот уже занят или инструктор не работает в это время');
        }
        const phone = String(body.client_phone || '').replace(/\D/g, '').slice(-10);
        const client = snapshot.clients?.find(item => (phone && String(item.phone || '').replace(/\D/g, '').slice(-10) === phone)
            || (!phone && body.client_name && item.name === body.client_name));
        if (client && (snapshot.bookings || []).filter(item => String(item.client_id) === String(client.id)
            && item.date === body.booking_date && ['pending', 'cancellation_pending', 'reschedule_pending', 'planned', 'confirmed'].includes(item.status)).length >= 2) {
            throw new Error('Максимум 2 записи на один день для одного клиента');
        }
    }
    const certificateBooking = path.match(/^\/bookings\/(-?\d+)\/apply-certificate$/);
    if (method === 'POST' && certificateBooking) {
        const booking = snapshot.bookings?.find(item => String(item.id) === certificateBooking[1]);
        const certificate = snapshot.data?.['/certificates']?.find(item =>
            String(item.code || '').toUpperCase() === String(body?.certificate_code || '').toUpperCase());
        if (!booking) throw new Error('Запись не найдена в локальной базе');
        if (!certificate) throw new Error('Сертификат не найден');
        if (booking.certificate_id) throw new Error('К этой записи уже применён сертификат');
        if (Number(certificate.nominal) !== Number(booking.price)) throw new Error('Номинал сертификата не совпадает с ценой услуги');
        if (certificate.is_used || Number(certificate.remaining) < Number(booking.price)) throw new Error('На сертификате недостаточно средств');
    }
    if (method === 'POST' && path === '/clients/assign-package') {
        const packageItem = snapshot.data?.['/packages']?.find(item => String(item.id) === String(body?.package_id));
        if (!packageItem?.is_available) throw new Error('Этот пакет уже выдан или недоступен');
    }
    if (method === 'POST' && path === '/clients/activate-certificate') {
        const certificate = snapshot.data?.['/certificates']?.find(item =>
            String(item.code || '').toUpperCase() === String(body?.certificate_code || '').toUpperCase());
        if (!certificate) throw new Error('Сертификат не найден');
        if (certificate.is_used || Number(certificate.remaining) <= 0) throw new Error('Сертификат уже использован');
        if (certificate.activated_by_client_id && String(certificate.activated_by_client_id) !== String(body.client_id)) {
            throw new Error('Сертификат уже активирован для другого клиента');
        }
    }
}
function createOperationId() {
    return `${Date.now()}-${crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)}`;
}

async function queueOfflineOperation(method, path, body, operationId = null) {
    // Read-state telemetry is intentionally not replayed; it has no business
    // value and must never prevent an offline administrative action.
    if (path.includes('/mark-viewed')) return { ok: true, offline: true };
    await validateOfflineOperation(method, path, body);
    const id = operationId || createOperationId();
    const createMatch = method === 'POST' && path.match(/^\/(instructors|clients|packages|certificates|faq|waiting-list)$/);
    const localId = createMatch ? -(Date.now() * 1000 + Math.floor(Math.random() * 1000)) : null;
    let localBody = body ? { ...body } : body;
    let localResult = {};
    let localClientId = null;
    if (path === '/bookings/manual' && body) {
        const snapshot = await offlineRead('api-cache', 'offline-snapshot');
        const phoneDigits = String(body.client_phone || '').replace(/\D/g, '').slice(-10);
        const existingClient = snapshot?.clients?.find(client =>
            (phoneDigits && String(client.phone || '').replace(/\D/g, '').slice(-10) === phoneDigits)
            || (!phoneDigits && body.client_name && client.name === body.client_name));
        localClientId = existingClient?.id ?? -(Date.now() * 1000 + Math.floor(Math.random() * 1000));
        localResult = { local_client_id: localClientId };
    }
    if (path === '/certificates' && localId) {
        const code = `OFF-${String(Math.abs(localId)).slice(-8)}`;
        localBody = { ...localBody, code, remaining: body.nominal, is_used: false, created_at: new Date().toISOString() };
        localResult = { code, nominal: body.nominal };
    }
    if (path === '/packages' && localId) {
        const code = `OFF-PKG-${String(Math.abs(localId)).slice(-6)}`;
        localBody = { ...localBody, code, is_active: true, is_available: true, bonus_exam: true };
        localResult = { code };
    }
    if (path === '/clients' && localBody) {
        // The password is needed only by the durable sync operation. It must
        // never become part of the readable local client database.
        const { password, ...clientRecord } = localBody;
        localBody = { ...clientRecord, bookings_count: 0, packages: [], certificates: [], created_at: new Date().toISOString() };
    }
    const bookingConfirm = path.match(/^\/bookings\/(-?\d+)\/confirm$/);
    if (bookingConfirm && body?.action === 'confirm') {
        localResult = { booking_number: `OFF-${bookingConfirm[1].replace('-', '')}` };
    }
    if (/^\/bookings\/-?\d+\/apply-certificate$/.test(path)) {
        const snapshot = await offlineRead('api-cache', 'offline-snapshot');
        const certificate = snapshot?.data?.['/certificates']?.find(item =>
            String(item.code || '').toUpperCase() === String(body?.certificate_code || '').toUpperCase());
        localResult = { amount: certificate?.remaining ?? certificate?.nominal ?? 0 };
    }
    if (path === '/bookings/check-pending-conflicts') {
        const snapshot = await offlineRead('api-cache', 'offline-snapshot');
        localResult = { merged_count: 0, conflicts_count: snapshot?.data?.['/bookings/conflicts']?.groups?.length || 0 };
    }
    await offlineStore('operations', id, {
        id, method, path, body, local_id: localId, local_client_id: localClientId,
        local_code: localResult.code || null,
        created_at: new Date().toISOString(),
    });
    await applyQueuedMutationToSnapshot(method, path, localBody, localId);
    setOfflineState(true);
    return { ok: true, offline: true, queued_operation_id: id, id: localId, ...localResult };
}

async function applyQueuedMutationToSnapshot(method, path, body, localId = null) {
    const match = path.match(/^\/bookings\/(-?\d+)(?:\/(edit|status|confirm))?$/);
    const snapshot = await offlineRead('api-cache', 'offline-snapshot');
    if (!snapshot) return;
    const supportReply = path.match(/^\/support\/(instructors\/)?dialogs\/(-?\d+)\/reply$/);
    if (supportReply && method === 'POST') {
        const isInstructor = Boolean(supportReply[1]);
        const targetId = Number(supportReply[2]);
        const messages = snapshot.data?.['/support-messages'] || [];
        messages.push({
            id: -(Date.now() * 1000 + Math.floor(Math.random() * 1000)),
            user_id: null,
            client_id: isInstructor ? null : targetId,
            instructor_id: isInstructor ? targetId : null,
            channel: isInstructor ? 'instructor' : 'client',
            sender: 'admin', text: String(body?.text || ''), is_read: false,
            is_admin_read: true, created_at: new Date().toISOString(),
        });
        snapshot.data['/support-messages'] = messages;
        await offlineStore('api-cache', 'offline-snapshot', snapshot);
        await offlineStore('api-cache', '/support-messages', messages);
        return;
    }
    if (match) {
      const booking = snapshot.bookings?.find(item => String(item.id) === match[1]);
      if (!booking) return;
      if (method === 'DELETE') booking.status = 'cancelled';
      if (match[2] === 'status' && body?.status) booking.status = body.status;
      if (match[2] === 'confirm' && body?.action) {
        booking.status = body.action === 'confirm' ? 'confirmed' : 'cancelled';
        booking.admin_confirmed = body.action === 'confirm';
        if (body.action === 'confirm' && !booking.booking_number) booking.booking_number = `OFF-${String(booking.id).replace('-', '')}`;
      }
      if (match[2] === 'edit' && body) {
        if (body.new_date) booking.date = body.new_date;
        if (body.new_start_time) {
            booking.start_time = body.new_start_time;
            const duration = booking.service_type === 'exam'
                ? (snapshot.slot_rules?.exam_duration_minutes || 20)
                : (snapshot.slot_rules?.training_duration_minutes || 60);
            booking.end_time = formatMinutes(minutesFromTime(body.new_start_time) + duration);
        }
        if (body.new_transmission) booking.transmission = body.new_transmission;
        if (body.new_instructor_id) {
            booking.instructor_id = body.new_instructor_id;
            booking.instructor_name = snapshot.instructors?.find(i => String(i.id) === String(body.new_instructor_id))?.name || booking.instructor_name;
        }
      }
      await offlineStore('api-cache', 'offline-snapshot', snapshot);
      await offlineStore('api-cache', '/bookings', snapshot.bookings);
      return;
    }
    const certificateRequestMutation = path.match(/^\/certificate-requests\/(-?\d+)\/confirm$/);
    if (certificateRequestMutation && method === 'POST') {
        const requests = snapshot.data?.['/certificate-requests']?.items || [];
        const requestIndex = requests.findIndex(item => String(item.id) === certificateRequestMutation[1]);
        if (requestIndex >= 0) requests.splice(requestIndex, 1);
        await offlineStore('api-cache', 'offline-snapshot', snapshot);
        await offlineStore('api-cache', '/certificate-requests', snapshot.data['/certificate-requests']);
        return;
    }
    const applyCertificateMutation = path.match(/^\/bookings\/(-?\d+)\/apply-certificate$/);
    if (applyCertificateMutation && method === 'POST') {
        const booking = snapshot.bookings?.find(item => String(item.id) === applyCertificateMutation[1]);
        const certificate = snapshot.data?.['/certificates']?.find(item =>
            String(item.code || '').toUpperCase() === String(body?.certificate_code || '').toUpperCase());
        if (booking && certificate) {
            const amount = Math.min(certificate.remaining ?? certificate.nominal, booking.price ?? booking.base_price ?? 0);
            booking.certificate_id = certificate.id;
            booking.certificate_amount = amount;
            booking.price = Math.max(0, (booking.price ?? booking.base_price ?? 0) - amount);
            booking.payment_status = booking.price === 0 ? 'paid' : 'partial';
            certificate.remaining = Math.max(0, (certificate.remaining ?? certificate.nominal) - amount);
            certificate.is_used = certificate.remaining === 0;
        }
        await offlineStore('api-cache', 'offline-snapshot', snapshot);
        await offlineStore('api-cache', '/bookings', snapshot.bookings);
        await offlineStore('api-cache', '/certificates', snapshot.data?.['/certificates'] || []);
        return;
    }
    if (path === '/bookings/resolve-conflict' && method === 'POST') {
        for (const bookingId of body?.booking_ids || []) {
            const booking = snapshot.bookings?.find(item => String(item.id) === String(bookingId));
            if (!booking) continue;
            booking.status = body.action === 'confirm' ? 'confirmed' : 'cancelled';
            booking.conflict_reason = null;
        }
        const groups = snapshot.data?.['/bookings/conflicts']?.groups || [];
        snapshot.data['/bookings/conflicts'].groups = groups.map(group => ({
            ...group,
            bookings: group.bookings.filter(item => !(body?.booking_ids || []).some(id => String(id) === String(item.id))),
        })).filter(group => group.bookings.length > 1);
        await offlineStore('api-cache', 'offline-snapshot', snapshot);
        await offlineStore('api-cache', '/bookings', snapshot.bookings);
        await offlineStore('api-cache', '/bookings/conflicts', snapshot.data['/bookings/conflicts']);
        return;
    }
    if (path === '/clients/assign-package' && method === 'POST') {
        const client = snapshot.clients?.find(item => String(item.id) === String(body?.client_id));
        const packageItem = snapshot.data?.['/packages']?.find(item => String(item.id) === String(body?.package_id));
        if (client && packageItem) {
            client.packages ||= [];
            client.packages.push({
                package_id: packageItem.id, name: packageItem.name, code: packageItem.code,
                sessions_count: packageItem.sessions_count, remaining_sessions: packageItem.sessions_count,
                remaining_bonus_exams: packageItem.bonus_exam ? 1 : 0, is_active: true,
            });
            Object.assign(packageItem, {
                assigned_client_id: client.id, assigned_client_name: client.name,
                assigned_client_phone: client.phone, is_available: false,
            });
        }
        await offlineStore('api-cache', 'offline-snapshot', snapshot);
        await offlineStore('api-cache', '/clients', snapshot.clients);
        await offlineStore('api-cache', '/packages', snapshot.data?.['/packages'] || []);
        return;
    }
    if (path === '/clients/activate-certificate' && method === 'POST') {
        const client = snapshot.clients?.find(item => String(item.id) === String(body?.client_id));
        const certificate = snapshot.data?.['/certificates']?.find(item =>
            String(item.code || '').toUpperCase() === String(body?.certificate_code || '').toUpperCase());
        if (client && certificate) {
            client.certificates ||= [];
            if (!client.certificates.some(item => item.id === certificate.id)) client.certificates.push({ ...certificate });
            certificate.client_name = client.name;
            certificate.client_phone = client.phone;
            certificate.activated_by_client_id = client.id;
        }
        await offlineStore('api-cache', 'offline-snapshot', snapshot);
        await offlineStore('api-cache', '/clients', snapshot.clients);
        await offlineStore('api-cache', '/certificates', snapshot.data?.['/certificates'] || []);
        return;
    }
    const dayOffMutation = path.match(/^\/instructors\/(-?\d+)\/days-off$/);
    if (dayOffMutation && method === 'PUT') {
        const rows = snapshot.data?.['/instructor-days-off'] || [];
        snapshot.data['/instructor-days-off'] = rows.filter(item => String(item.instructor_id) !== dayOffMutation[1]);
        for (const date of body?.days_off_dates || []) snapshot.data['/instructor-days-off'].push({ instructor_id: Number(dayOffMutation[1]), day_off_date: date });
        await offlineStore('api-cache', 'offline-snapshot', snapshot);
        await offlineStore('api-cache', '/instructor-days-off', snapshot.data['/instructor-days-off']);
        return;
    }
    const scheduleMutation = path.match(/^\/instructors\/(-?\d+)\/daily-schedules(?:\/([^/]+))?$/);
    if (scheduleMutation) {
        const rows = snapshot.data?.['/instructor-daily-schedules'] || [];
        const scheduleDate = scheduleMutation[2] || body?.schedule_date;
        snapshot.data['/instructor-daily-schedules'] = rows.filter(item => !(String(item.instructor_id) === scheduleMutation[1] && item.schedule_date === scheduleDate));
        if (method === 'PUT') snapshot.data['/instructor-daily-schedules'].push({ instructor_id: Number(scheduleMutation[1]), ...body });
        await offlineStore('api-cache', 'offline-snapshot', snapshot);
        await offlineStore('api-cache', '/instructor-daily-schedules', snapshot.data['/instructor-daily-schedules']);
        return;
    }
    const entity = path.match(/^\/(instructors|clients|packages|certificates|faq|waiting-list)(?:\/(-?\d+))?/);
    if (!entity) return;
    const cacheKey = `/${entity[1]}`;
    const cached = snapshot.data?.[cacheKey];
    const items = entity[1] === 'waiting-list' ? cached?.items : cached;
    if (!Array.isArray(items)) return;
    const targetId = entity[2];
    if (method === 'POST' && !targetId) {
        items.unshift({ id: localId, ...body, status: body?.status || (entity[1] === 'waiting-list' ? 'waiting' : undefined), is_active: body?.is_active ?? true });
    } else {
        const index = items.findIndex(item => String(item.id) === String(targetId));
        if (index < 0) return;
        if (method === 'DELETE') items.splice(index, 1);
        else if (method === 'PUT') {
            if (path.endsWith('/status') && body?.action) items[index].status = body.action;
            else Object.assign(items[index], body || {});
        }
    }
    if (entity[1] === 'instructors' && (body?.is_duty || body?.is_lead)) {
        for (const instructor of items) {
            if (String(instructor.id) === String(targetId ?? localId)) continue;
            if (body.is_duty) instructor.is_duty = false;
            if (body.is_lead) instructor.is_lead = false;
        }
    }
    if (method === 'DELETE' && entity[1] === 'instructors') {
        snapshot.bookings = (snapshot.bookings || []).map(item =>
            String(item.instructor_id) === String(targetId)
                ? { ...item, instructor_id: null, instructor_name: null }
                : item
        );
        snapshot.data['/offline-mobile-bookings'] = (snapshot.data?.['/offline-mobile-bookings'] || [])
            .map(item => String(item.instructor_id) === String(targetId)
                ? { ...item, instructor_id: null, instructor_name: null }
                : item);
        snapshot.data['/instructor-days-off'] = (snapshot.data?.['/instructor-days-off'] || [])
            .filter(item => String(item.instructor_id) !== String(targetId));
        snapshot.data['/instructor-daily-schedules'] = (snapshot.data?.['/instructor-daily-schedules'] || [])
            .filter(item => String(item.instructor_id) !== String(targetId));
        snapshot.data['/support-messages'] = (snapshot.data?.['/support-messages'] || [])
            .map(item => String(item.instructor_id) === String(targetId)
                ? { ...item, instructor_id: null, instructor_name: null }
                : item);
    }
    if (method === 'DELETE' && entity[1] === 'clients') {
        snapshot.bookings = (snapshot.bookings || []).filter(item => String(item.client_id) !== String(targetId));
        snapshot.data['/support-messages'] = (snapshot.data?.['/support-messages'] || [])
            .filter(item => String(item.client_id) !== String(targetId));
        const removedCertificateIds = new Set((snapshot.data?.['/certificates'] || [])
            .filter(certificate => String(certificate.activated_by_client_id) === String(targetId)
                || String(certificate.used_by_user_id) === String(targetId))
            .map(certificate => String(certificate.id)));
        snapshot.data['/certificates'] = (snapshot.data?.['/certificates'] || [])
            .filter(certificate => !removedCertificateIds.has(String(certificate.id)));
        const certificateRequests = snapshot.data?.['/certificate-requests'];
        if (Array.isArray(certificateRequests?.items)) {
            certificateRequests.items = certificateRequests.items.filter(request =>
                String(request.client_id) !== String(targetId)
                && !removedCertificateIds.has(String(request.matched_certificate_id)));
        }
        snapshot.data['/packages'] = (snapshot.data?.['/packages'] || [])
            .filter(packageItem => String(packageItem.assigned_client_id) !== String(targetId));
    }
    if (entity[1] === 'instructors') snapshot.instructors = items;
    if (entity[1] === 'clients') snapshot.clients = items;
    await offlineStore('api-cache', 'offline-snapshot', snapshot);
    await offlineStore('api-cache', cacheKey, cached);
    if (method === 'DELETE' && ['instructors', 'clients'].includes(entity[1])) {
        await offlineStore('api-cache', '/bookings', snapshot.bookings);
        await offlineStore('api-cache', '/support-messages', snapshot.data['/support-messages']);
        if (entity[1] === 'clients') {
            await offlineStore('api-cache', '/certificates', snapshot.data['/certificates']);
            await offlineStore('api-cache', '/certificate-requests', snapshot.data['/certificate-requests']);
            await offlineStore('api-cache', '/packages', snapshot.data['/packages']);
        }
    }
}
async function syncOfflineOperations() {
    const operations = await offlineOperations();
    if (!operations.length) return true;
    if (isAdminOffline) return false;
    const res = await fetch(`${API}/offline-sync`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include',
        body: JSON.stringify(operations),
    });
    if (!res.ok) throw new Error('Не удалось синхронизировать офлайн-изменения');
    const data = await res.json();
    const byId = new Map(operations.map(op => [op.id, op]));
    for (const result of data.results || []) {
        if (result.status === 'ok') await removeOfflineOperation(result.id);
        if (result.status === 'error') {
            const original = byId.get(result.id);
            if (original) await updateOfflineOperation({
                ...original, sync_error: result.detail || 'Сервер не принял изменение',
                last_sync_attempt: new Date().toISOString(),
            });
        }
    }
    await persistOfflineIdMappings(data.local_id_map || {}, data.local_value_map || {});
    const failed = (data.results || []).filter(r => r.status === 'error');
    const current = await offlineOperations();
    renderOfflineIssues(current);
    const signature = failed.map(r => `${r.id}:${r.detail || ''}`).join('|');
    if (failed.length) {
        if (signature !== lastOfflineIssueSignature) {
            lastOfflineIssueSignature = signature;
            showToast(`${failed.length} офлайн-записей требуют внимания`, 'error');
        }
        return false;
    }
    lastOfflineIssueSignature = '';
    if (operations.length) showToast('Офлайн-изменения синхронизированы');
    return true;
}
async function heartbeat() {
    if (offlineHeartbeatInFlight) return;
    offlineHeartbeatInFlight = true;
    try {
        const res = await fetch(`${API}/check-session`, { credentials: 'include', cache: 'no-store' });
        if (res.status === 401) {
            showLogin('Сессия завершена. Войдите снова.');
            return;
        }
        if (!res.ok) throw new Error(`Проверка соединения вернула ${res.status}`);
        setOfflineState(false);
        const queuedBeforeHeartbeat = await offlineOperations();
        if (queuedBeforeHeartbeat.length) {
            offlineReplayInProgress = true;
            offlineReplayPromise = (async () => {
                const syncCompleted = await syncOfflineOperations();
                const operationsQueuedDuringSync = (await offlineOperations()).length > 0;
                if (syncCompleted && !operationsQueuedDuringSync) await refreshOfflineSnapshot();
            })().catch((error) => {
                console.error('Не удалось синхронизировать офлайн-изменения', error);
            });
            try {
                await offlineReplayPromise;
            } finally {
                offlineReplayInProgress = false;
                offlineReplayPromise = null;
                setOfflineState(false);
            }
        } else {
            // The 30-second snapshot is an atomic background cache replacement.
            // It neither blocks nor reroutes current online admin actions.
            await refreshOfflineSnapshot();
        }
    } catch (error) {
        if (!navigator.onLine) setOfflineState(true);
        else console.error('Проверка соединения с админкой не прошла', error);
    } finally {
        offlineHeartbeatInFlight = false;
    }
}
function startOfflineMonitoring() {
    heartbeat();
    offlineOperations().then(renderOfflineIssues).catch(() => {});
    if (offlineHeartbeatTimer) clearInterval(offlineHeartbeatTimer);
    offlineHeartbeatTimer = setInterval(heartbeat, 30000);
    window.addEventListener('online', heartbeat);
    window.addEventListener('offline', () => setOfflineState(true));
}

async function refreshOfflineSnapshot() {
    // Replace the old snapshot only after a complete fresh copy has arrived.
    // Do not merge endpoint-by-endpoint: stale keys must not survive a
    // reconnection or a recurring 30-second refresh.
    if (isAdminOffline) return;
    try {
        const res = await fetch(`${API}/offline-snapshot`, { credentials: 'include', cache: 'no-store' });
        if (!res.ok) throw new Error('Не удалось получить полный офлайн-снимок');
        const snapshot = await res.json();
        const snapshotBookings = snapshot?.data?.['/bookings'];
        const hasCompleteBookingLinks = Array.isArray(snapshotBookings)
            && snapshotBookings.every(booking => booking.instructor_id !== null && booking.instructor_id !== undefined);
        const requiredPaths = ['/bookings', '/instructors', '/clients', '/packages', '/certificates', '/faq',
            '/notifications', '/waiting-list', '/audit-logs', '/instructor-daily-schedules',
            '/instructor-days-off', '/offline-mobile-bookings', '/dashboard', '/notification-counts',
            '/analytics/heatmap', '/analytics/instructor-load', '/analytics/booking-sources', '/analytics/gender',
            '/analytics/revenue', '/certificate-requests', '/bookings/conflicts'];
        const hasFullData = snapshot?.version >= 12 && requiredPaths.every(path => snapshot.data[path] !== undefined);
        if (!snapshot?.slot_rules || !snapshot?.data || !hasCompleteBookingLinks || !hasFullData) {
            throw new Error('Сервер вернул неполный офлайн-снимок');
        }
        await replaceOfflineApiCache(snapshot);
    } catch (error) {
        // Keep the previous complete snapshot if a new one failed.  It is
        // never mixed with a partial refresh and is replaced on the next
        // successful 30-second cycle.
        console.warn('Офлайн-снимок не обновлён:', error);
    }
}

function minutesFromTime(value) {
    const [hours, minutes] = String(value || '00:00').slice(0, 5).split(':').map(Number);
    return hours * 60 + minutes;
}
function formatMinutes(value) {
    return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}
function currentKzClock() {
    const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Almaty', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date()).reduce((out, part) => ({ ...out, [part.type]: part.value }), {});
    return { date: `${parts.year}-${parts.month}-${parts.day}`, minutes: Number(parts.hour) * 60 + Number(parts.minute) };
}
async function buildOfflineSlots(bookingDate, serviceType, transmission, selectedInstructorId) {
    const snapshot = await offlineRead('api-cache', 'offline-snapshot');
    if (!snapshot?.instructors || !snapshot?.bookings) {
        throw new Error('Нет локальной копии расписания. Сначала откройте админку при наличии интернета.');
    }
    // There is deliberately no fetch in this function. Every field change in
    // the offline manual-booking form reads the temporary local database and
    // recalculates only the requested instructor/date/КПП view.
    const rules = snapshot.slot_rules || {};
    const trainingDuration = rules.training_duration_minutes || 60;
    const examDuration = rules.exam_duration_minutes || 20;
    const duration = serviceType === 'exam' ? examDuration : trainingDuration;
    const capacity = rules.capacity || 6;
    // Old snapshots have no fleet key. Preserve their existing capacity
    // behavior until the next online refresh, while new snapshots apply the
    // exact transmission and repair status stored by the server.
    const fleet = Array.isArray(snapshot.vehicles) ? snapshot.vehicles : [];
    const availableVehicles = fleet.filter(vehicle =>
        vehicle.transmission === transmission && !vehicle.is_under_repair
    );
    const vehicleCapacity = fleet.length ? availableVehicles.length : capacity;
    const location = rules.location || 'Циолковского 30';
    const mobileBookings = snapshot.data?.['/offline-mobile-bookings'] || [];
    const allBookings = [...snapshot.bookings, ...mobileBookings];
    const activeBookings = allBookings.filter(booking => {
        const statuses = booking.is_mobile
            ? ['pending', 'cancellation_pending', 'reschedule_pending', 'planned', 'confirmed']
            : ['pending', 'cancellation_pending', 'reschedule_pending', 'planned', 'confirmed', 'in_progress'];
        return booking.date === bookingDate && statuses.includes(booking.status);
    });
    const allInstructors = snapshot.instructors.filter(instructor => instructor.is_active !== false);
    const instructors = selectedInstructorId
        ? allInstructors.filter(instructor => String(instructor.id) === String(selectedInstructorId))
        : allInstructors;
    const dailySchedules = new Map((snapshot.data?.['/instructor-daily-schedules'] || []).map(item => [
        `${item.instructor_id}:${item.schedule_date}`, item,
    ]));
    const daysOff = new Set((snapshot.data?.['/instructor-days-off'] || []).map(item =>
        `${item.instructor_id}:${item.day_off_date}`
    ));
    const weekdayNames = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
    const targetWeekday = weekdayNames[new Date(`${bookingDate}T12:00:00`).getDay()];
    const isOverlapping = (start, end, otherStart, otherEnd) => start < otherEnd && end > otherStart;
    const bookingEnd = booking => minutesFromTime(booking.end_time || formatMinutes(
        minutesFromTime(booking.start_time) + (booking.service_type === 'exam' ? examDuration : trainingDuration)
    ));
    const scheduleFor = instructor => {
        const key = `${instructor.id}:${bookingDate}`;
        const daily = dailySchedules.get(key);
        if (daily?.is_day_off || (!daily && daysOff.has(key))) return null;
        const start = daily?.working_hours_start || instructor.working_hours_start;
        const end = daily?.working_hours_end || instructor.working_hours_end;
        if (!start || !end) return null;
        return {
            start: minutesFromTime(start), end: minutesFromTime(end),
            // A per-day row fully overrides lunch. Null means no lunch for
            // that date, matching get_effective_schedule on the server.
            lunchStart: daily ? daily.lunch_start : instructor.lunch_start,
            lunchEnd: daily ? daily.lunch_end : instructor.lunch_end,
        };
    };
    const schedules = new Map(instructors.map(instructor => [instructor.id, scheduleFor(instructor)]));
    const validSchedules = [...schedules.values()].filter(Boolean);
    if (!validSchedules.length) {
        return { date: bookingDate, service_type: serviceType, transmission, instructor_id: selectedInstructorId || null, slots: [], offline: true };
    }
    const now = currentKzClock();
    const startHour = Math.min(...validSchedules.map(schedule => Math.floor(schedule.start / 60)));
    const endHour = Math.min(Math.max(...validSchedules.map(schedule => Math.floor(schedule.end / 60))), 21);
    if (bookingDate === now.date && (Math.floor(now.minutes / 60) > endHour || (
        Math.floor(now.minutes / 60) === endHour && now.minutes % 60 >= 1
    ))) {
        return { date: bookingDate, service_type: serviceType, transmission, instructor_id: selectedInstructorId || null, slots: [], offline: true };
    }
    const slots = [];
    for (let start = startHour * 60; start <= endHour * 60; start += duration) {
        const end = start + duration;
        if (bookingDate === now.date && start <= now.minutes) continue;
        const busyIds = new Set(activeBookings
            .filter(booking => isOverlapping(start, end, minutesFromTime(booking.start_time), bookingEnd(booking)))
            .map(booking => String(booking.instructor_id))
        );
        const bookedAtLocation = activeBookings.filter(booking =>
            booking.location === location && isOverlapping(
                start, end, minutesFromTime(booking.start_time), bookingEnd(booking)
            )
        ).length;
        const bookedCompatibleVehicles = activeBookings.filter(booking =>
            booking.transmission === transmission && isOverlapping(
                start, end, minutesFromTime(booking.start_time), bookingEnd(booking)
            )
        ).length;
        const slotBookings = activeBookings.filter(booking =>
            (!selectedInstructorId || String(booking.instructor_id) === String(selectedInstructorId))
            && isOverlapping(start, end, minutesFromTime(booking.start_time), bookingEnd(booking))
        );
        const instructorIsAvailable = (instructor, allowDuty) => {
            if (instructor.is_duty && !allowDuty) return false;
            if (busyIds.has(String(instructor.id))) return false;
            if (!['both', serviceType].includes(String(instructor.lesson_type || 'both'))) return false;
            if (transmission === 'manual' && !['manual', 'both'].includes(instructor.transmission)) return false;
            if (transmission === 'automatic' && !['automatic', 'both'].includes(instructor.transmission)) return false;
            if (String(instructor.days_off || '').split(',').map(day => day.trim()).includes(targetWeekday)) return false;
            const schedule = schedules.get(instructor.id);
            if (!schedule || schedule.start > start || schedule.end < start) return false;
            const lunchStart = minutesFromTime(schedule.lunchStart);
            const lunchEnd = minutesFromTime(schedule.lunchEnd);
            const hasLunch = schedule.lunchStart && schedule.lunchEnd && lunchStart !== lunchEnd
                && !(lunchStart === 0 && lunchEnd === 0);
            return !hasLunch || !isOverlapping(start, end, lunchStart, lunchEnd);
        };
        let available = instructors.filter(instructor => instructorIsAvailable(instructor, Boolean(selectedInstructorId)));
        if (!selectedInstructorId) {
            available = available.filter(instructor => !instructor.is_duty);
            if (!available.length) {
                available = instructors.filter(instructor => instructorIsAvailable(instructor, true)).slice(0, 1);
            }
        }
        const availableIds = available.map(instructor => instructor.id);
        slots.push({
            time: formatMinutes(start), end_time: formatMinutes(end),
            bookings: slotBookings.map(booking => ({
                client: booking.client_name || 'Клиент', instructor: booking.instructor_name || '—',
                instructor_id: booking.instructor_id, status: booking.status,
            })),
            booked_count: bookedAtLocation, capacity,
            available_instructors_count: availableIds.length,
            available_instructor_ids: availableIds,
            recommended_instructor_id: availableIds[0] || null,
            is_free: availableIds.length > 0
                && bookedAtLocation < capacity
                && bookedCompatibleVehicles < vehicleCapacity,
        });
    }
    return { date: bookingDate, service_type: serviceType, transmission, instructor_id: selectedInstructorId || null, slots, offline: true };
}

async function offlineBookingList(path) {
    const snapshot = await offlineRead('api-cache', 'offline-snapshot');
    if (!snapshot?.bookings) return null;
    const operations = await offlineOperations();
    const items = [...snapshot.bookings];
    for (const operation of operations.filter(op => op.path === '/bookings/manual' && op.body)) {
        const body = operation.body;
        const existing = items.find(b => b.source === 'offline' && b.date === body.booking_date
            && String(b.start_time).slice(0, 5) === body.start_time && String(b.instructor_id || '') === String(body.instructor_id || ''));
        const instructor = snapshot.instructors?.find(i => String(i.id) === String(body.instructor_id));
        const item = existing || {
            id: `offline-${operation.id}`, source: 'offline', client_name: body.client_name || body.client_phone || 'Клиент',
            client_phone: body.client_phone || '', instructor_id: body.instructor_id,
            instructor_name: instructor?.name || 'Назначается', service_type: body.service_type,
            transmission: body.transmission, location: body.location || 'Циолковского 30', date: body.booking_date,
            start_time: body.start_time, end_time: formatMinutes(minutesFromTime(body.start_time) + 60),
            status: 'confirmed', price: body.service_type === 'exam' ? 5000 : 10000,
        };
        if (!existing) items.push(item);
        if (operation.sync_error) {
            item.status = 'conflict';
            item.conflict_reason = `Офлайн-запись не синхронизирована: ${operation.sync_error}`;
        }
    }
    const query = new URLSearchParams(path.split('?')[1] || '');
    const statuses = query.get('status')?.split(',').filter(Boolean);
    const filtered = items.filter(item => {
        if (statuses && !statuses.includes(item.status)) return false;
        if (query.get('date_from') && item.date < query.get('date_from')) return false;
        if (query.get('date_to') && item.date > query.get('date_to')) return false;
        return true;
    });
    return filtered.sort((a, b) => `${a.date} ${a.start_time}`.localeCompare(`${b.date} ${b.start_time}`));
}

async function offlineDerivedRead(path) {
    const snapshot = await offlineRead('api-cache', 'offline-snapshot');
    if (!snapshot) return undefined;
    const localBookings = snapshot.bookings || [];
    if (path === '/dashboard') {
        const today = currentKzClock().date;
        const weekAgo = addKzDays(today, -7);
        const monthAgo = addKzDays(today, -30);
        const completedRevenue = from => localBookings
            .filter(item => item.status === 'completed' && item.date >= from)
            .reduce((sum, item) => sum + Number(item.price || 0), 0);
        return {
            revenue_today: localBookings.filter(item => item.status === 'completed' && item.date === today)
                .reduce((sum, item) => sum + Number(item.price || 0), 0),
            revenue_week: completedRevenue(weekAgo), revenue_month: completedRevenue(monthAgo),
            total_bookings: localBookings.length,
            cancelled: localBookings.filter(item => item.status === 'cancelled').length,
            no_shows: localBookings.filter(item => item.status === 'no_show').length,
            clients_count: snapshot.clients?.length || 0,
            instructors_count: snapshot.instructors?.length || 0,
        };
    }
    if (path === '/analytics/heatmap') {
        const grouped = new Map();
        for (const booking of localBookings.filter(item => ['confirmed', 'completed'].includes(item.status))) {
            const hour = Number(String(booking.start_time || '00:00').slice(0, 2));
            const key = `${booking.date}:${hour}`;
            grouped.set(key, (grouped.get(key) || 0) + 1);
        }
        const dayNames = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
        return [...grouped.entries()].map(([key, count]) => {
            const separator = key.lastIndexOf(':');
            const date = key.slice(0, separator);
            return { date, hour: Number(key.slice(separator + 1)), count,
                day_name: dayNames[new Date(`${date}T12:00:00`).getDay()] };
        }).sort((a, b) => `${a.date}:${a.hour}`.localeCompare(`${b.date}:${b.hour}`));
    }
    if (path === '/analytics/instructor-load') {
        const since = addKzDays(currentKzClock().date, -30);
        const counts = new Map();
        for (const booking of localBookings.filter(item => item.date >= since && ['confirmed', 'completed'].includes(item.status))) {
            const name = booking.instructor_name || '—';
            counts.set(name, (counts.get(name) || 0) + 1);
        }
        return [...counts.entries()].map(([name, bookings]) => ({ name, bookings }));
    }
    if (path === '/analytics/booking-sources') {
        const counts = { telegram: 0, mobile: 0, manual: 0 };
        for (const booking of localBookings) {
            const source = String(booking.source || '').toLowerCase();
            if (source === 'telegram') counts.telegram += 1;
            else if (source === 'mobile') counts.mobile += 1;
            else if (['manual', 'admin', 'admin_offline', 'offline'].includes(source)) counts.manual += 1;
        }
        const total = localBookings.length;
        const item = key => ({ count: counts[key], percent: total ? Math.round(counts[key] * 1000 / total) / 10 : 0 });
        return { total, telegram: item('telegram'), mobile: item('mobile'), manual: item('manual'),
            unknown: Math.max(0, total - counts.telegram - counts.mobile - counts.manual) };
    }
    if (path === '/notification-counts') {
        return { ...(snapshot.data?.['/notification-counts'] || {}),
            pending_applications_count: localBookings.filter(item => ['pending', 'cancellation_pending', 'reschedule_pending'].includes(item.status)).length,
            conflicts_count: localBookings.filter(item => ['conflict', 'disputed'].includes(item.status)).length };
    }
    if (path === '/booking-window') return snapshot.booking_window;
    if (path.startsWith('/clients/search?')) {
        const query = (new URLSearchParams(path.split('?')[1]).get('q') || '').toLowerCase();
        return (snapshot.clients || []).filter(c => `${c.name} ${c.phone || ''}`.toLowerCase().includes(query)).slice(0, 10);
    }
    if (path.startsWith('/clients/') && path.endsWith('/bookings')) {
        const clientId = path.split('/')[2];
        return (snapshot.bookings || []).filter(b => String(b.client_id) === clientId).map(b => ({
            ...b,
            booking_date: b.booking_date || b.date,
            certificate_code: snapshot.data?.['/certificates']?.find(c => String(c.id) === String(b.certificate_id))?.code || null,
        }));
    }
    if (path.startsWith('/clients/') && path.endsWith('/history')) {
        const clientId = path.split('/')[2];
        return (snapshot.bookings || []).filter(b => String(b.client_id) === clientId).map(b => ({
            type: 'booking', icon: '📅', title: `Запись: ${b.status}`, date: b.created_at || b.date,
            description: `${b.date} ${String(b.start_time).slice(0, 5)} · ${b.instructor_name || '—'}`,
            status: b.status,
        }));
    }
    if (path === '/support/dialogs' || path === '/support/instructors/dialogs') {
        const isInstructor = path.includes('/instructors/');
        const key = isInstructor ? 'instructor_id' : 'client_id';
        const people = isInstructor ? snapshot.instructors || [] : snapshot.clients || [];
        const rows = (snapshot.data?.['/support-messages'] || []).filter(m => m[key]);
        const lastByPerson = new Map(rows.map(m => [m[key], m]));
        const ids = isInstructor ? people.map(person => person.id) : [...lastByPerson.keys()];
        return ids.map(id => {
            const last = lastByPerson.get(id);
            const person = people.find(p => String(p.id) === String(id)) || {};
            const unread = rows.filter(message => String(message[key]) === String(id)
                && message.sender === (isInstructor ? 'instructor' : 'user')
                && !message.is_admin_read).length;
            return { user_id: id, user_name: person.name || 'Пользователь', user_phone: person.phone || '',
                telegram_id: person.telegram_id, telegram_username: person.telegram_username,
                last_message: last?.text || (isInstructor ? 'Можно написать инструктору' : ''),
                last_message_at: last?.created_at || null, unread_from_user: unread, has_new: unread > 0, channel: last?.channel };
        }).sort((a, b) => String(b.last_message_at || '').localeCompare(String(a.last_message_at || '')));
    }
    const dialogMatch = path.match(/^\/support\/(?:instructors\/)?dialogs\/(\d+)$/);
    if (dialogMatch) {
        const isInstructor = path.includes('/instructors/');
        const key = isInstructor ? 'instructor_id' : 'client_id';
        const people = isInstructor ? snapshot.instructors || [] : snapshot.clients || [];
        const user = people.find(p => String(p.id) === dialogMatch[1]) || {};
        return { user: { id: user.id, name: user.name || 'Пользователь', phone: user.phone || '',
                telegram_id: user.telegram_id,
                created_at: user.created_at },
            messages: (snapshot.data?.['/support-messages'] || []).filter(m => String(m[key]) === dialogMatch[1])
                .map(m => ({ ...m, sender: isInstructor && m.sender !== 'admin' ? 'user' : m.sender })),
            recent_bookings: isInstructor ? [] : localBookings.filter(b => String(b.client_id) === dialogMatch[1]).slice(-10).reverse()
                .map(b => ({ id: b.id, booking_date: b.date, start_time: String(b.start_time).slice(0, 5),
                    service_type: b.service_type, status: b.status, price: b.price })) };
    }
    const daysOffMatch = path.match(/^\/instructors\/(-?\d+)\/days-off$/);
    if (daysOffMatch) return (snapshot.data?.['/instructor-days-off'] || [])
        .filter(x => String(x.instructor_id) === daysOffMatch[1])
        .map(x => ({ id: x.id, date: x.date || x.day_off_date }));
    const schedulesMatch = path.match(/^\/instructors\/(-?\d+)\/daily-schedules$/);
    if (schedulesMatch) return (snapshot.data?.['/instructor-daily-schedules'] || []).filter(x => String(x.instructor_id) === schedulesMatch[1]);
    const matchingMatch = path.match(/^\/waiting-list\/matching\/(\d+)$/);
    if (matchingMatch) {
        const booking = (snapshot.bookings || []).find(item => String(item.id) === matchingMatch[1]);
        const waiting = snapshot.data?.['/waiting-list']?.items || [];
        if (!booking) return { items: [] };
        return { items: waiting.filter(item => item.status === 'waiting'
            && (!item.desired_date || item.desired_date === booking.date)
            && (!item.transmission || item.transmission === booking.transmission)
            && (!item.instructor_id || String(item.instructor_id) === String(booking.instructor_id))) };
    }
    const textMatch = path.match(/^\/bookings\/(\d+)\/(?:card-text|copy-text|reminder-text)$/);
    if (textMatch) {
        const booking = (snapshot.bookings || []).find(item => String(item.id) === textMatch[1]);
        if (!booking) return { text: '' };
        if (path.endsWith('/reminder-text')) {
            const transmission = booking.transmission === 'manual' ? 'Механика' : 'Автомат';
            const service = booking.service_type === 'training' ? 'Обучение вождению' : 'Пробный экзамен';
            return { text: `🔔 Напоминание о записи!\nВаше занятие уже через 1 час.\n📋 Номер записи: ${booking.booking_number || '—'}\n📍 Адрес: ${booking.location || '—'}\n⏰ Время: ${String(booking.start_time || '').slice(0, 5)}\n🚗 Программа: ${service} (${transmission})\n👨‍🏫 Инструктор: ${booking.instructor_name || 'Не назначен'}\n💵 Оплатить занятие можно наличными или через Kaspi QR.\n⏱️ Пожалуйста, не опаздывайте.\n🚦 Хорошего занятия!` };
        }
        return { text: `${booking.date} ${String(booking.start_time).slice(0, 5)}–${String(booking.end_time).slice(0, 5)}\n${booking.client_name || 'Клиент'}\n${booking.instructor_name || '—'}\n${booking.location || ''}` };
    }
    return undefined;
}

// --- Escape HTML ---
function escapeHtml(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g, '&#39;');
}

// --- Notification Badge ---
function updateNavBadge(page, count) {
    const btn = document.querySelector(`.nav-item[data-page="${page}"]`);
    if (!btn) return;
    let badge = btn.querySelector('.nav-badge');
    if (count > 0) {
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'nav-badge';
            btn.appendChild(badge);
        }
        badge.textContent = count > 99 ? '99+' : String(count);
        badge.classList.add('nav-badge-pulse');
    } else if (badge) {
        badge.remove();
    }
}

function waitForApplicationAlertEnd() {
    return new Promise((resolve) => {
        const finish = () => {
            applicationAlertAudio.removeEventListener('ended', finish);
            applicationAlertAudio.removeEventListener('error', finish);
            applicationAlertAudio.removeEventListener('pause', finish);
            resolve();
        };
        applicationAlertAudio.addEventListener('ended', finish, { once: true });
        applicationAlertAudio.addEventListener('error', finish, { once: true });
        applicationAlertAudio.addEventListener('pause', finish, { once: true });
    });
}

function stopApplicationAlert() {
    _applicationAlertPlaybackId += 1;
    applicationAlertAudio.pause();
    applicationAlertAudio.currentTime = 0;
    _applicationAlertPlayback = null;
}

function updateApplicationSoundToggle() {
    const button = document.getElementById('application-sound-toggle');
    if (!button) return;
    button.textContent = applicationSoundEnabled ? '🔊' : '🔇';
    button.setAttribute('aria-pressed', String(applicationSoundEnabled));
    button.setAttribute('aria-label', applicationSoundEnabled ? 'Выключить звук новых заявок' : 'Включить звук новых заявок');
    button.title = applicationSoundEnabled ? 'Звук новых заявок включён. Нажмите, чтобы выключить.' : 'Звук новых заявок выключен. Нажмите, чтобы включить.';
}

function toggleApplicationSound() {
    applicationSoundEnabled = !applicationSoundEnabled;
    try {
        localStorage.setItem(APPLICATION_SOUND_STORAGE_KEY, applicationSoundEnabled ? 'on' : 'off');
    } catch {
        // Если хранилище браузера недоступно, переключатель работает до обновления страницы.
    }
    if (!applicationSoundEnabled) stopApplicationAlert();
    updateApplicationSoundToggle();
}

async function playNewApplicationAlert() {
    if (!applicationSoundEnabled || _applicationAlertPlayback) return _applicationAlertPlayback;

    const playbackId = ++_applicationAlertPlaybackId;
    const playback = (async () => {
        for (let attempt = 0; attempt < 3 && playbackId === _applicationAlertPlaybackId && applicationSoundEnabled; attempt += 1) {
            try {
                applicationAlertAudio.currentTime = 0;
                await applicationAlertAudio.play();
            } catch {
                break;
            }
            await waitForApplicationAlertEnd();
        }
    })();

    _applicationAlertPlayback = playback;
    try {
        await playback;
    } finally {
        if (playbackId === _applicationAlertPlaybackId) _applicationAlertPlayback = null;
    }
}

function resetApplicationAlertState() {
    _notificationCountsInitialized = false;
    _notificationPollSequence += 1;
    stopApplicationAlert();
}

async function pollNotificationCounts() {
    try {
        const requestViewVersion = _badgeViewVersion;
        const requestSequence = ++_notificationPollSequence;
        const counts = await apiGet('/notification-counts');
        if (!counts) return;
        // Ignore a response which was requested before a tab was marked read.
        // Otherwise a stale polling response can recreate a cleared badge.
        if (requestViewVersion !== _badgeViewVersion || requestSequence !== _notificationPollSequence) return;
        const pendingApplicationsCount = counts.pending_applications_count || 0;
        const hasNewApplication = _notificationCountsInitialized
            && pendingApplicationsCount > (_prevCounts.pending_applications_count || 0);
        const clientsBtn = document.querySelector('.nav-item[data-page="clients"] .nav-badge');
        if (counts.new_clients > _prevCounts.new_clients && clientsBtn) {
            clientsBtn.classList.remove('nav-badge-pulse');
            void clientsBtn.offsetWidth;
            clientsBtn.classList.add('nav-badge-pulse');
        }
        const supportBtn = document.querySelector('.nav-item[data-page="support"] .nav-badge');
        if (counts.unread_support > _prevCounts.unread_support && supportBtn) {
            supportBtn.classList.remove('nav-badge-pulse');
            void supportBtn.offsetWidth;
            supportBtn.classList.add('nav-badge-pulse');
        }
        const bookingsAttention = (counts.pending_applications_count || 0) + (counts.conflicts_count || 0);
        const previousBookingsAttention = (_prevCounts.pending_applications_count || 0) + (_prevCounts.conflicts_count || 0);
        const bookingsBtn = document.querySelector('.nav-item[data-page="bookings"] .nav-badge');
        if (bookingsAttention > previousBookingsAttention && bookingsBtn) {
            bookingsBtn.classList.remove('nav-badge-pulse');
            void bookingsBtn.offsetWidth;
            bookingsBtn.classList.add('nav-badge-pulse');
        }
        const notifBtn = document.querySelector('.nav-item[data-page="notifications"] .nav-badge');
        if (counts.unread_notifications > _prevCounts.unread_notifications && notifBtn) {
            notifBtn.classList.remove('nav-badge-pulse');
            void notifBtn.offsetWidth;
            notifBtn.classList.add('nav-badge-pulse');
        }
        _prevCounts = counts;
        updateNavBadge('clients', counts.new_clients);
        updateNavBadge('support', counts.unread_support);
        updateNavBadge('bookings', bookingsAttention);
        updateNavBadge('notifications', counts.unread_notifications);
        updateApplicationsAttention((counts.pending_applications_count || 0) > 0);
        const headerDot = document.getElementById('header-notification-dot');
        if (headerDot) {
            const hasUnread = Object.values(counts).some(Number);
            headerDot.classList.toggle('hidden', !hasUnread);
        }
        if (hasNewApplication) void playNewApplicationAlert();
        refreshWaitingAttention().catch(() => {});
        refreshConflictsAttention().catch(() => {});
    } catch(e) { /* silent */ }
}

function startBadgePolling() {
    _notificationCountsInitialized = false;
    _notificationPollSequence += 1;
    pollNotificationCounts();
    if (_badgePollInterval) clearInterval(_badgePollInterval);
    _badgePollInterval = setInterval(pollNotificationCounts, 20000);
}

// --- Session Validation ---
let _sessionPollInterval = null;

async function validateSession() {
    if (isAdminOffline) return;
    try {
        const res = await fetch(`${API}/check-session`, { credentials: 'include', cache: 'no-store' });
        const data = res.ok ? await res.json().catch(() => null) : null;
        if (!res.ok || data?.ok !== true) {
            // Сессия невалидна (сменили пароль на другом устройстве) - разлогиниваем
            showLogin('Сессия завершена. Войдите снова.');
        }
    } catch { /* silent — не дергаем при ошибке сети */ }
}

function startSessionPolling() {
    validateSession();
    if (_sessionPollInterval) clearInterval(_sessionPollInterval);
    _sessionPollInterval = setInterval(validateSession, 60000);
}

// --- Mobile Sidebar ---
function closeMobileSidebar() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    sidebar.classList.remove('open');
    document.body.classList.remove('sidebar-open');
}

// --- Toast ---
function showToast(msg, type = 'success') {
    const t = document.getElementById('toast');
    if (!t) return;
    const icon = type === 'success' ? '✓ ' : (type === 'error' ? '✕ ' : 'ℹ ');
    t.innerHTML = `<span style="font-weight:700;">${icon}</span> <span>${escapeHtml(msg)}</span>`;
    t.className = `toast ${type}`;
    t.classList.remove('hidden');
    clearTimeout(t._timeout);
    t._timeout = setTimeout(() => t.classList.add('hidden'), 3500);
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
            credentials: 'include',
            body: JSON.stringify({
                username: document.getElementById('username').value,
                password: document.getElementById('password').value,
            }),
        });
        if (!res.ok) { errEl.textContent = 'Неверный логин или пароль'; errEl.classList.remove('hidden'); return; }
        showDashboard();
    } catch { errEl.textContent = 'Ошибка соединения'; errEl.classList.remove('hidden'); }
});

function showDashboard() {
    document.getElementById('login-screen').classList.remove('active');
    document.getElementById('dashboard-screen').classList.add('active');
    updateApplicationSoundToggle();
    startBadgePolling();
    startSessionPolling();
    startOfflineMonitoring();
    initPage();
}

function showLogin(message = '') {
    resetApplicationAlertState();
    if (_badgePollInterval) clearInterval(_badgePollInterval);
    if (_sessionPollInterval) clearInterval(_sessionPollInterval);
    if (supportPollInterval) clearInterval(supportPollInterval);
    _badgePollInterval = null;
    _sessionPollInterval = null;
    supportPollInterval = null;
    if (offlineHeartbeatTimer) clearInterval(offlineHeartbeatTimer);
    offlineHeartbeatTimer = null;
    currentDialogUserId = null;
    closeMobileSidebar();

    const loginScreen = document.getElementById('login-screen');
    const dashboardScreen = document.getElementById('dashboard-screen');
    const errEl = document.getElementById('login-error');
    const passwordInput = document.getElementById('password');

    dashboardScreen?.classList.remove('active');
    loginScreen?.classList.add('active');
    if (passwordInput) passwordInput.value = '';
    if (errEl) {
        errEl.textContent = message;
        errEl.classList.toggle('hidden', !message);
    }
    if (window.location.hash) {
        history.replaceState(null, '', window.location.pathname + window.location.search);
    }
}

// --- Navigation ---
const pageTitles = {
    dashboard: 'Дашборд', bookings: 'Записи', instructors: 'Инструкторы', vehicles: 'Автопарк', clients: 'Клиенты',
    analytics: 'Аналитика', packages: 'Сертификаты', support: 'Поддержка',
    faq: 'FAQ', archive: 'Архив', notifications: 'События', audit: 'Журнал действий', settings: 'Настройки',
};

async function navigateTo(page) {
    // Old bookmarks used the former top-level waiting-list page. Keep them
    // useful, but show the list inside "Записи" where it belongs.
    if (page === 'waiting-list') {
        page = 'bookings';
        currentBookingTab = 'waiting-list';
    }
    if (page === 'conflicts') {
        page = 'bookings';
        currentBookingTab = 'conflicts';
    }
    document.querySelectorAll('.nav-item[data-page]').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`.nav-item[data-page="${page}"]`);
    if (btn) btn.classList.add('active');
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${page}`).classList.add('active');
    document.getElementById('page-title').textContent = pageTitles[page] || page;
    // Сохраняем страницу в URL hash
    window.location.hash = page;
    // Close mobile sidebar
    closeMobileSidebar();
    // Останавливаем поллинг поддержки при выходе
    if (page !== 'support') {
        if (supportPollInterval) {
            clearInterval(supportPollInterval);
            supportPollInterval = null;
        }
        // При следующем входе в поддержку не открываем старый диалог
        // автоматически: именно открытие конкретного чата подтверждает чтение.
        currentDialogUserId = null;
    }
    if (page !== 'bookings') clearCompletedArchiveRefresh();
    if (page !== 'analytics') clearAnalyticsRevenueRefresh();
    // Вкладка поддержки не считается прочитанной при открытии: это происходит
    // только в API конкретного диалога. Для остальных вкладок фиксируем просмотр.
    const viewedCountKey = { bookings: 'unread_bookings', notifications: 'unread_notifications', clients: 'new_clients' };
    const viewedPath = { bookings: '/bookings/mark-viewed', notifications: '/notifications/mark-viewed', clients: '/clients/mark-viewed' };
    if (viewedPath[page]) {
        try {
            await apiPost(viewedPath[page], {});
            _badgeViewVersion += 1;
            _prevCounts[viewedCountKey[page]] = 0;
            updateNavBadge(page, 0);
        } catch (e) {
            console.error('Не удалось отметить вкладку просмотренной', e);
        }
    }
    // Load data
    const loaders = { dashboard: loadDashboard, bookings: loadBookings, instructors: loadInstructors, vehicles: loadVehicles, clients: loadClients, analytics: loadAnalytics, packages: loadPackages, support: loadSupport, faq: loadFaq, notifications: loadNotifications, audit: loadAudit };
    if (loaders[page]) {
        Promise.resolve(loaders[page]()).catch((e) => {
            console.error(`Не удалось загрузить вкладку ${page}`, e);
            showToast(e.message || 'Не удалось загрузить данные вкладки', 'error');
        });
    }
    // Запускаем поллинг поддержки при входе
    if (page === 'support') {
        if (supportPollInterval) clearInterval(supportPollInterval);
        supportPollInterval = setInterval(() => {
            if (currentDialogUserId) openDialog(currentDialogUserId);
            else loadSupport();
        }, 8000);
    }
}

document.querySelectorAll('.nav-item[data-page]').forEach(btn => {
    btn.addEventListener('click', () => navigateTo(btn.dataset.page));
});

// Восстанавливаем страницу из URL hash после загрузки
window.addEventListener('hashchange', () => {
    const page = window.location.hash.substring(1);
    if (page && pageTitles[page]) {
        navigateTo(page);
    }
});

// Инициализация страницы при загрузке
function initPage() {
    const hash = window.location.hash.substring(1);
    if (hash && pageTitles[hash]) {
        navigateTo(hash);
    } else {
        navigateTo('dashboard');
    }
}

document.getElementById('logout-btn').addEventListener('click', async () => {
    try {
        await fetch(`${API}/logout`, {
            method: 'POST',
            credentials: 'include'
        });
    } catch { /* Даже при сетевой ошибке закрываем локальный UI. */ }
    showLogin('Вы вышли из панели. Войдите снова.');
});

document.getElementById('mobile-menu-btn').addEventListener('click', () => {
    const sidebar = document.getElementById('sidebar');
    const isOpen = sidebar.classList.toggle('open');
    document.body.classList.toggle('sidebar-open', isOpen);
});

// Закрытие sidebar по клику на overlay (body)
document.body.addEventListener('click', (e) => {
    const sidebar = document.getElementById('sidebar');
    const menuBtn = document.getElementById('mobile-menu-btn');
    
    if (sidebar.classList.contains('open') && 
        !sidebar.contains(e.target) && 
        !menuBtn.contains(e.target)) {
        sidebar.classList.remove('open');
        document.body.classList.remove('sidebar-open');
    }
});

// --- API helpers ---
async function readOfflineApi(path) {
    if (path.startsWith('/support/')) {
        throw new Error('Переписка поддержки недоступна офлайн. Подключитесь к интернету.');
    }
    if (path === '/bookings/archive' || path.startsWith('/logs/archive/')) {
        throw new Error('Архив доступен только онлайн. Подключитесь к интернету.');
    }
    if (path === '/bookings' || path.startsWith('/bookings?')) {
        const offlineBookings = await offlineBookingList(path);
        if (offlineBookings) return offlineBookings;
    }
    const derived = await offlineDerivedRead(path);
    if (derived !== undefined) return derived;
    const cached = await offlineRead('api-cache', path);
    if (cached !== undefined) return cached;
    throw new Error('Нет локальных данных для этого раздела. Откройте админку онлайн, чтобы получить полный снимок.');
}

async function apiGet(path) {
    // A normal background snapshot is always parallel. Waiting happens only
    // after a real reconnect while queued offline operations are replayed.
    if (offlineReplayInProgress) await waitForOfflineReplay();
    if (isAdminOffline) return readOfflineApi(path);
    try {
        const res = await fetch(`${API}${path}`, { credentials: 'include' });
        if (res.status === 401) { showLogin('Сессия завершена. Войдите снова.'); throw new Error('Сессия завершена. Войдите снова.'); }
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Ошибка загрузки данных'); }
        const data = await res.json();
        return data;
    } catch (error) {
        // Never mask a real server validation error with stale data.
        if (!(error instanceof TypeError)) throw error;
        if (navigator.onLine) throw new Error('Не удалось связаться с сервером админки. Повторите действие.');
        setOfflineState(true);
        return readOfflineApi(path);
    }
}
function apiPost(path, data) {
    const signature = `POST:${path}:${JSON.stringify(data ?? null)}`;
    const existing = adminMutationInFlight.get(signature);
    if (existing) return existing;
    const operationId = createOperationId();
    const pending = apiPostOnce(path, data, operationId).finally(() => {
        if (adminMutationInFlight.get(signature) === pending) adminMutationInFlight.delete(signature);
    });
    adminMutationInFlight.set(signature, pending);
    return pending;
}

async function apiPostOnce(path, data, operationId) {
    if (offlineReplayInProgress) await waitForOfflineReplay();
    if (isAdminOffline) {
        if (path.includes('/mark-viewed')) return { ok: true, offline: true };
        assertOfflineWriteAllowed(path);
        return queueOfflineOperation('POST', path, data, operationId);
    }
    try {
        const res = await fetch(`${API}${path}`, { method: 'POST', headers: {'Content-Type': 'application/json', 'X-Idempotency-Key': operationId}, body: JSON.stringify(data), credentials: 'include' });
        if (res.status === 401) { showLogin('Сессия завершена. Войдите снова.'); throw new Error('Сессия завершена. Войдите снова.'); }
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Ошибка'); }
        return res.json();
    } catch (error) {
        if (!(error instanceof TypeError)) throw error;
        assertOfflineWriteAllowed(path);
        return queueOfflineOperation('POST', path, data, operationId);
    }
}
async function apiPut(path, data) {
    if (offlineReplayInProgress) await waitForOfflineReplay();
    if (isAdminOffline) {
        assertOfflineWriteAllowed(path);
        return queueOfflineOperation('PUT', path, data);
    }
    try {
        const res = await fetch(`${API}${path}`, { method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data), credentials: 'include' });
        if (res.status === 401) { showLogin('Сессия завершена. Войдите снова.'); throw new Error('Сессия завершена. Войдите снова.'); }
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Ошибка'); }
        return res.json();
    } catch (error) {
        if (!(error instanceof TypeError)) throw error;
        assertOfflineWriteAllowed(path);
        return queueOfflineOperation('PUT', path, data);
    }
}
async function apiDelete(path) {
    if (offlineReplayInProgress) await waitForOfflineReplay();
    if (isAdminOffline) {
        assertOfflineWriteAllowed(path);
        return queueOfflineOperation('DELETE', path, null);
    }
    try {
        const res = await fetch(`${API}${path}`, { method: 'DELETE', credentials: 'include' });
        if (res.status === 401) { showLogin('Сессия завершена. Войдите снова.'); throw new Error('Сессия завершена. Войдите снова.'); }
        if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || 'Ошибка'); }
        return res.json();
    } catch (error) {
        if (!(error instanceof TypeError)) throw error;
        assertOfflineWriteAllowed(path);
        return queueOfflineOperation('DELETE', path, null);
    }
}

function assertOfflineWriteAllowed(path) {
    if (path === '/bookings/cancelled') {
        throw new Error('Массовое удаление отменённых записей доступно только при подключении к серверу.');
    }
    const localEntities = /^\/(?:bookings|waiting-list|instructors|clients|packages|certificates|certificate-requests|faq)(?:\/|$)/;
    const externalOnly = /\/mark-viewed$/.test(path);
    if (!localEntities.test(path) || externalOnly) {
        throw new Error('Это действие недоступно офлайн. Подключитесь к интернету.');
    }
}

// --- Dashboard ---
async function loadDashboard() {
    const [data, bookings] = await Promise.all([
        apiGet('/dashboard'),
        apiGet('/bookings').catch(() => [])
    ]);
    if (!data) return;

    // 1. Hero 4 Metrics Grid
    const sg = document.getElementById('stats-grid');
    if (sg) {
        sg.innerHTML = `
            <div class="stat-card revenue">
                <div class="stat-header">
                    <span class="stat-title">Выручка (Месяц)</span>
                    <div class="stat-icon">₸</div>
                </div>
                <div class="stat-value">${(data.revenue_month || 0).toLocaleString('ru-RU')} ₸</div>
                <div class="stat-trend up">↗ Выручка за 30 дней</div>
            </div>
            <div class="stat-card primary">
                <div class="stat-header">
                    <span class="stat-title">Всего записей</span>
                    <div class="stat-icon">📅</div>
                </div>
                <div class="stat-value">${data.total_bookings || 0}</div>
                <div class="stat-trend up">Актуальная база занятий</div>
            </div>
            <div class="stat-card">
                <div class="stat-header">
                    <span class="stat-title">Клиентов в базе</span>
                    <div class="stat-icon">👤</div>
                </div>
                <div class="stat-value">${data.clients_count || 0}</div>
                <div class="stat-trend up">Зарегистрировано</div>
            </div>
            <div class="stat-card">
                <div class="stat-header">
                    <span class="stat-title">Инструкторов</span>
                    <div class="stat-icon">🚘</div>
                </div>
                <div class="stat-value">${data.instructors_count || 0}</div>
                <div class="stat-trend up">Штат специалистов</div>
            </div>
        `;
    }

    // 2. Secondary Mini Stats Bar
    const ssb = document.getElementById('secondary-stats-bar');
    if (ssb) {
        ssb.innerHTML = `
            <div class="mini-stat-card">
                <div class="mini-stat-label">Выручка сегодня</div>
                <div class="mini-stat-value" style="color:var(--success);">${(data.revenue_today || 0).toLocaleString('ru-RU')} ₸</div>
            </div>
            <div class="mini-stat-card">
                <div class="mini-stat-label">Выручка за неделю</div>
                <div class="mini-stat-value" style="color:var(--success);">${(data.revenue_week || 0).toLocaleString('ru-RU')} ₸</div>
            </div>
            <div class="mini-stat-card">
                <div class="mini-stat-label">Отменено</div>
                <div class="mini-stat-value" style="color:var(--danger);">${data.cancelled || 0}</div>
            </div>
            <div class="mini-stat-card">
                <div class="mini-stat-label">Не явились</div>
                <div class="mini-stat-value" style="color:var(--warning-text);">${data.no_shows || 0}</div>
            </div>
        `;
    }

    // 3. Today's Schedule Timeline Table
    const tbody = document.getElementById('dashboard-today-tbody');
    if (tbody) {
        const todayStr = new Date().toISOString().split('T')[0];
        const todayBookings = Array.isArray(bookings) 
            ? bookings.filter(b => b.booking_date === todayStr || b.date === todayStr)
            : [];
            
        if (todayBookings.length > 0) {
            tbody.innerHTML = todayBookings.map(b => {
                const serviceLabel = b.service_type === 'exam' ? 'Экзамен' : 'Обучение';
                const transLabel = b.transmission === 'automatic' ? 'АКПП' : 'МКПП';
                return `
                    <tr>
                        <td><strong style="color:var(--primary);">${escapeHtml(b.start_time || b.time || '—')}</strong></td>
                        <td>
                            <div style="font-weight:600;color:var(--text-main);">${escapeHtml(b.client_name || b.client || '—')}</div>
                            <div style="font-size:11.5px;color:var(--text-muted);">${escapeHtml(b.client_phone || '')}</div>
                        </td>
                        <td>${escapeHtml(b.instructor_name || b.instructor || '—')}</td>
                        <td>${escapeHtml(b.location || 'Циолковского 30')}</td>
                        <td><span class="badge badge-primary">${serviceLabel} · ${transLabel}</span></td>
                        <td>${statusBadge(b.status)}</td>
                    </tr>
                `;
            }).join('');
        } else if (Array.isArray(bookings) && bookings.length > 0) {
            const upcoming = bookings.slice(0, 5);
            tbody.innerHTML = upcoming.map(b => {
                const serviceLabel = b.service_type === 'exam' ? 'Экзамен' : 'Обучение';
                const transLabel = b.transmission === 'automatic' ? 'АКПП' : 'МКПП';
                return `
                    <tr>
                        <td>
                            <strong style="color:var(--primary);">${escapeHtml(b.start_time || b.time || '—')}</strong>
                            <div style="font-size:11px;color:var(--text-muted);">${escapeHtml(b.booking_date || b.date || '')}</div>
                        </td>
                        <td>
                            <div style="font-weight:600;color:var(--text-main);">${escapeHtml(b.client_name || b.client || '—')}</div>
                            <div style="font-size:11.5px;color:var(--text-muted);">${escapeHtml(b.client_phone || '')}</div>
                        </td>
                        <td>${escapeHtml(b.instructor_name || b.instructor || '—')}</td>
                        <td>${escapeHtml(b.location || 'Циолковского 30')}</td>
                        <td><span class="badge badge-primary">${serviceLabel} · ${transLabel}</span></td>
                        <td>${statusBadge(b.status)}</td>
                    </tr>
                `;
            }).join('');
        } else {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:24px;">На сегодня записей нет</td></tr>`;
        }
    }
}

// --- Bookings ---
const statusLabels = { pending:'Ожидает подтверждения', cancellation_pending:'Ожидает подтверждения отмены', reschedule_pending:'Ожидает подтверждения переноса', planned:'Запланирована', confirmed:'Подтверждена', completed:'Завершена', cancelled:'Отменена', no_show:'Не явился', conflict:'Конфликт', disputed:'Спорная' };
const statusBadge = (s) => `<span class="badge badge-${s}">${statusLabels[s]||s}</span>`;

let currentBookingTab = 'active';
let completedArchiveRefreshTimer = null;
let openWaitingEntryId = null;
let waitingAttentionAcknowledged = false;
let conflictsAttentionAcknowledged = false;
let lastKnownConflictsCount = 0;

function updateApplicationsAttention(hasApplications) {
    const tab = document.getElementById('tab-applications');
    if (!tab) return;
    tab.classList.toggle('applications-tab-attention', Boolean(hasApplications));
    tab.title = hasApplications ? 'Есть необработанные заявки' : '';
}

function switchBookingTab(tab) {
    currentBookingTab = tab;
    if (tab !== 'completed') clearCompletedArchiveRefresh();
    document.querySelectorAll('.booking-tab').forEach(t => {
        t.classList.remove('active');
        t.style.borderBottomColor = '';
        t.style.color = '';
    });
    const active = document.getElementById(`tab-${tab}`);
    if (active) {
        active.classList.add('active');
    }
    const isWaiting = tab === 'waiting-list';
    const isConflicts = tab === 'conflicts';
    document.getElementById('waiting-list-section')?.classList.toggle('hidden', !isWaiting);
    document.getElementById('conflicts-section')?.classList.toggle('hidden', !isConflicts);
    document.getElementById('bookings-list-section')?.classList.toggle('hidden', isWaiting || isConflicts);
    document.getElementById('booking-filter-controls')?.classList.toggle('hidden', isWaiting || isConflicts);
    document.getElementById('purge-cancelled-bookings-button')?.classList.toggle('hidden', tab !== 'cancelled');
    if (isWaiting) {
        waitingAttentionAcknowledged = true;
        updateWaitingAttention(false);
        loadWaitingList();
        return;
    }
    if (isConflicts) {
        conflictsAttentionAcknowledged = true;
        updateConflictsAttention(false);
        loadConflicts();
        return;
    }
    loadBookings();
}

async function loadBookings() {
    if (currentBookingTab === 'waiting-list') {
        document.getElementById('waiting-list-section')?.classList.remove('hidden');
        document.getElementById('bookings-list-section')?.classList.add('hidden');
        document.getElementById('booking-filter-controls')?.classList.add('hidden');
        return loadWaitingList();
    }
    if (currentBookingTab === 'conflicts') {
        document.getElementById('conflicts-section')?.classList.remove('hidden');
        document.getElementById('bookings-list-section')?.classList.add('hidden');
        document.getElementById('booking-filter-controls')?.classList.add('hidden');
        return loadConflicts();
    }
    document.getElementById('waiting-list-section')?.classList.add('hidden');
    document.getElementById('conflicts-section')?.classList.add('hidden');
    document.getElementById('bookings-list-section')?.classList.remove('hidden');
    document.getElementById('booking-filter-controls')?.classList.remove('hidden');
    const params = new URLSearchParams();
    const df = document.getElementById('filter-date-from').value;
    const dt = document.getElementById('filter-date-to').value;
    const loc = document.getElementById('filter-location').value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    if (loc) params.set('location', loc);

    if (currentBookingTab === 'active') {
        params.set('status', 'planned,confirmed');
    } else if (currentBookingTab === 'applications') {
        params.set('status', 'pending,cancellation_pending,reschedule_pending');
    } else if (currentBookingTab === 'completed') {
        params.set('status', 'completed,no_show');
    } else if (currentBookingTab === 'cancelled') {
        params.set('status', 'cancelled');
    }

    const data = await apiGet(`/bookings?${params}`);
    if (!data) return;
    scheduleCompletedArchiveRefresh(data);
    if (currentBookingTab === 'cancelled') refreshWaitingAttention();
    
    const tbody = document.querySelector('#bookings-table tbody');
    const mobileCards = document.getElementById('bookings-mobile-cards');
    
    // Rejected applications remain in the database for audit and client
    // notifications, but must not appear in the administrator's lists.
    const visibleBookings = currentBookingTab === 'cancelled'
        ? data.filter(booking => booking.admin_confirmed !== false)
        : data;

    if (!visibleBookings.length) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-secondary);padding:32px">\u041d\u0435\u0442 \u0437\u0430\u043f\u0438\u0441\u0435\u0439</td></tr>';
        if (mobileCards) mobileCards.innerHTML = '<p style="color:var(--text-secondary);text-align:center;padding:32px">\u041d\u0435\u0442 \u0437\u0430\u043f\u0438\u0441\u0435\u0439</p>';
        return; 
    }
    
    const isEditable = currentBookingTab === 'active';
    
    tbody.innerHTML = visibleBookings.map(b => {
        const sourceBadge = b.source === 'mobile'
            ? '<span class="badge badge-primary" style="margin-left:6px" title="\u0421\u043e\u0437\u0434\u0430\u043d\u043e \u0432 \u043c\u043e\u0431\u0438\u043b\u044c\u043d\u043e\u043c \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0438">\ud83d\udcf1</span>'
            : b.source === 'telegram'
            ? '<span class="badge badge-info" style="margin-left:6px" title="Telegram-бот">\u2708\ufe0f</span>'
            : '';
        const numberLine = b.booking_number ? `<br><small style="color:var(--primary);font-weight:bold">#${b.booking_number}</small>` : '';
        const actionId = JSON.stringify(String(b.id));
        const isLocalOffline = String(b.id).startsWith('offline-');
        const isPendingConfirm = b.status === 'pending' || b.status === 'conflict' || b.status === 'disputed';
        const isPending = pendingDeletes[b.id] ? 'pending-delete' : '';
        const btnText = pendingDeletes[b.id] ? '\u2713 \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c' : '\u041e\u0442\u043c\u0435\u043d\u0438\u0442\u044c';
        const telegramWriteButton = b.source === 'telegram' && b.client_id
            ? `<button class="btn btn-primary btn-sm" onclick="openClientChat(${b.client_id})" style="font-size:0.72rem">💬 Написать</button>` : '';
        let actions = '';
        if (isLocalOffline) {
            const reason = b.conflict_reason ? `<br><small style="color:#b45309">${escapeHtml(b.conflict_reason)}</small>` : '<br><small style="color:var(--text-secondary)">Офлайн-запись ожидает синхронизации</small>';
            actions = `<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap"><button class="btn btn-outline-danger btn-sm" onclick="deleteBooking(${actionId})" style="font-size:0.72rem">Отменить</button>${reason}</div>`;
        } else if (b.status === 'cancellation_pending') {
            actions = `<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap"><button class="btn btn-success btn-sm" onclick="confirmCancellation(${b.id})">✅ Подтвердить отмену</button><button class="btn btn-outline btn-sm" onclick="rejectCancellation(${b.id})">↩️ Отклонить</button></div>`;
        } else if (b.status === 'reschedule_pending') {
            const requested = `${b.requested_reschedule_date || '—'} ${b.requested_reschedule_start_time || ''}`.trim();
            actions = `<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap"><small style="color:var(--primary)">Перенос на: ${escapeHtml(requested)}</small><button class="btn btn-success btn-sm" onclick="resolveRescheduleRequest(${b.id}, 'confirm')">✅ Подтвердить перенос</button><button class="btn btn-outline-danger btn-sm" onclick="resolveRescheduleRequest(${b.id}, 'reject')">↩️ Отклонить</button></div>`;
        } else if (isPendingConfirm) {
            const conflictInfo = b.conflict_reason ? `<br><small style="color:red">${escapeHtml(b.conflict_reason)}</small>` : '';
            actions = `
            <div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap">
                <button class="btn btn-success btn-sm" onclick="confirmBooking(${b.id})" style="font-size:0.72rem">\u2705 Подтвердить</button>
                <button class="btn btn-outline-danger btn-sm" onclick="rejectBooking(${b.id})" style="font-size:0.72rem">\u274c Отклонить</button>
                <button class="btn btn-outline btn-sm" onclick="copyBookingCard(${b.id})" style="font-size:0.72rem" title="Карточка записи">\ud83d\udccb</button>
                <button class="btn btn-outline btn-sm" onclick="copyBookingReminder(${b.id})" style="font-size:0.72rem" title="Скопировать напоминание">🔔</button>
                ${conflictInfo}
            </div>`;
        } else if (isEditable) {
            actions = `
            <div style="display:flex;align-items:center;gap:5px">
                <button class="btn btn-outline btn-icon btn-sm" onclick="editBooking(${b.id})" title="\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c">\u270f\ufe0f</button>
                <button class="btn btn-outline btn-sm" onclick="copyBookingCard(${b.id})" style="font-size:0.72rem" title="Карточка записи">\ud83d\udccb</button>
                <button class="btn btn-outline btn-sm" onclick="copyBookingReminder(${b.id})" style="font-size:0.72rem" title="Скопировать напоминание">🔔</button>
                ${telegramWriteButton}
                <button class="btn btn-outline-danger btn-sm" onclick="deleteBooking(${b.id})" style="font-size:0.72rem">${btnText}</button>
            </div>`;
        } else if (currentBookingTab === 'cancelled') {
            actions = `<div style="display:flex;gap:5px"><button class="btn btn-outline btn-sm" onclick="copySlotText(${b.id})" title="Скопировать предложение для листа ожидания">📋 Предложить слот</button><button class="btn btn-outline-danger btn-sm" onclick="purgeCancelledBooking(${b.id})">🗑️ Удалить</button></div>`;
        }
        const rowStyle = isPendingConfirm ? 'style="background:#fff3cd"' : '';
        return `<tr class="${isPending} booking-reminder-row" data-booking-start="${b.date}T${b.start_time.slice(0,5)}:00" ${rowStyle}>
        <td>${b.date}${numberLine}</td>
        <td class="booking-time-cell"><strong>${b.start_time.slice(0,5)}</strong><small class="booking-countdown" style="color:var(--text-secondary)"></small></td>
        <td><strong>${escapeHtml(b.client_name)}</strong>${sourceBadge}<br><small style="color:var(--text-secondary)">${escapeHtml(b.client_phone||'')}</small></td>
        <td>${b.instructor_name}</td>
        <td>${b.transmission === 'manual' ? 'МКПП' : b.transmission === 'automatic' ? 'АКПП' : escapeHtml(b.transmission || '—')}</td>
        <td>${b.service_type==='training'?'\u041e\u0431\u0443\u0447\u0435\u043d\u0438\u0435':'\u042d\u043a\u0437\u0430\u043c\u0435\u043d'}</td>
        <td><small>${b.location}</small></td>
        <td>${statusBadge(b.status)}</td>
        <td>${paymentSummary(b)}</td>
        <td>${actions}</td>
    </tr>`;
    }).join('');
    
    if (mobileCards) {
        mobileCards.innerHTML = visibleBookings.map(b => {
            const sourceBadge = b.source === 'mobile' ? '\ud83d\udcf1 ' : '';
            const numberLine = b.booking_number
                ? `<br><small style="color:var(--primary);font-weight:bold">#${b.booking_number}</small>`
                : '';
            const actionId = JSON.stringify(String(b.id));
            const isLocalOffline = String(b.id).startsWith('offline-');
            const isPendingConfirm = b.status === 'pending' || b.status === 'conflict' || b.status === 'disputed';
            const isPending = pendingDeletes[b.id] ? 'pending-delete' : '';
            const btnText = pendingDeletes[b.id] ? '\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c' : '\u041e\u0442\u043c\u0435\u043d\u0438\u0442\u044c';
            const telegramWriteButton = b.source === 'telegram' && b.client_id
                ? `<button class="btn btn-primary btn-sm" onclick="openClientChat(${b.client_id})" style="flex:1">💬 Написать</button>` : '';
            
            let actions = '';
            if (isLocalOffline) {
                const reason = b.conflict_reason ? `<div style="color:#b45309;font-size:11px;margin-top:5px">${escapeHtml(b.conflict_reason)}</div>` : '';
                actions = `<button class="btn btn-outline-danger btn-sm" onclick="deleteBooking(${actionId})" style="flex:1">Отменить</button>${reason}`;
            } else if (b.status === 'cancellation_pending') {
                actions = `<button class="btn btn-success btn-sm" onclick="confirmCancellation(${b.id})" style="flex:1">✅ Подтвердить отмену</button><button class="btn btn-outline btn-sm" onclick="rejectCancellation(${b.id})" style="flex:1">↩️ Отклонить</button>`;
            } else if (b.status === 'reschedule_pending') {
                const requested = `${b.requested_reschedule_date || '—'} ${b.requested_reschedule_start_time || ''}`.trim();
                actions = `<div style="font-size:11px;color:var(--primary);margin-bottom:6px">Перенос на: ${escapeHtml(requested)}</div><button class="btn btn-success btn-sm" onclick="resolveRescheduleRequest(${b.id}, 'confirm')" style="flex:1">✅ Подтвердить</button><button class="btn btn-outline-danger btn-sm" onclick="resolveRescheduleRequest(${b.id}, 'reject')" style="flex:1">↩️ Отклонить</button>`;
            } else if (isPendingConfirm) {
                const conflictInfo = b.conflict_reason ? `<div style="color:red;font-size:11px;margin-top:5px">${escapeHtml(b.conflict_reason)}</div>` : '';
                actions = `
                    <button class="btn btn-success btn-sm" onclick="confirmBooking(${b.id})" style="flex:1">\u2705 \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c</button>
                    <button class="btn btn-outline-danger btn-sm" onclick="rejectBooking(${b.id})" style="flex:1">\u274c \u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c</button>
                    <button class="btn btn-outline btn-sm" onclick="copyBookingCard(${b.id})" style="flex:1" title="\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0430 \u0437\u0430\u043f\u0438\u0441\u0438">\ud83d\udccb</button>
                    <button class="btn btn-outline btn-sm" onclick="copyBookingReminder(${b.id})" style="flex:1" title="Скопировать напоминание">🔔</button>
                    ${conflictInfo}
                `;
            } else if (isEditable) {
                actions = `
                    <button class="btn btn-outline btn-sm" onclick="editBooking(${b.id})" style="flex:1">\u270f\ufe0f \u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c</button>
                    <button class="btn btn-outline btn-sm" onclick="copyBookingReminder(${b.id})" style="flex:1" title="Скопировать напоминание">🔔</button>
                    ${telegramWriteButton}
                    <button class="btn btn-outline-danger btn-sm" onclick="deleteBooking(${b.id})" style="flex:1">${btnText}</button>
                `;
            } else if (currentBookingTab === 'cancelled') {
                actions = `<button class="btn btn-outline btn-sm" onclick="copySlotText(${b.id})" style="flex:1">📋 Предложить слот</button><button class="btn btn-outline-danger btn-sm" onclick="purgeCancelledBooking(${b.id})" style="flex:1">🗑️ Удалить</button>`;
            }
            
            const cardStyle = isPendingConfirm ? 'style="background:#fff3cd"' : '';
            return `
                <div class="mobile-card ${isPending}" ${cardStyle}>
                    <div class="mobile-card-header">
                        <div>
                            <div class="mobile-card-title">${sourceBadge}${escapeHtml(b.client_name)}${numberLine}</div>
                            <div style="font-size:12px;color:var(--text-secondary);margin-top:2px">${b.client_phone || ''}</div>
                        </div>
                        ${statusBadge(b.status)}
                    </div>
                    <div class="mobile-card-row">
                        <span class="mobile-card-label">\u0414\u0430\u0442\u0430:</span>
                        <span class="mobile-card-value"><strong>${b.date}</strong></span>
                    </div>
                    <div class="mobile-card-row">
                        <span class="mobile-card-label">\u0412\u0440\u0435\u043c\u044f:</span>
                        <span class="mobile-card-value">${b.start_time.slice(0,5)}</span>
                    </div>
                    <div class="mobile-card-row">
                        <span class="mobile-card-label">\u0418\u043d\u0441\u0442\u0440\u0443\u043a\u0442\u043e\u0440:</span>
                        <span class="mobile-card-value">${b.instructor_name}</span>
                    </div>
                    <div class="mobile-card-row">
                        <span class="mobile-card-label">КПП:</span>
                        <span class="mobile-card-value">${b.transmission === 'manual' ? 'МКПП' : b.transmission === 'automatic' ? 'АКПП' : escapeHtml(b.transmission || '—')}</span>
                    </div>
                    <div class="mobile-card-row">
                        <span class="mobile-card-label">\u0423\u0441\u043b\u0443\u0433\u0430:</span>
                        <span class="mobile-card-value">${b.service_type==='training'?'\u041e\u0431\u0443\u0447\u0435\u043d\u0438\u0435':'\u042d\u043a\u0437\u0430\u043c\u0435\u043d'}</span>
                    </div>
                    <div class="mobile-card-row">
                        <span class="mobile-card-label">\u041f\u043b\u043e\u0449\u0430\u0434\u043a\u0430:</span>
                        <span class="mobile-card-value">${b.location}</span>
                    </div>
                    <div class="mobile-card-row">
                        <span class="mobile-card-label">\u0426\u0435\u043d\u0430:</span>
                        <span class="mobile-card-value">${paymentSummary(b)}</span>
                    </div>
                    ${actions ? `<div style="display:flex;gap:8px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border-color)">${actions}</div>` : ''}
                </div>
            `;
        }).join('');
    }
}

function clearCompletedArchiveRefresh() {
    if (completedArchiveRefreshTimer) {
        clearTimeout(completedArchiveRefreshTimer);
        completedArchiveRefreshTimer = null;
    }
}

function scheduleCompletedArchiveRefresh(bookings) {
    clearCompletedArchiveRefresh();
    if (currentBookingTab !== 'completed' || !Array.isArray(bookings)) return;
    // Возраст архива считается по дате занятия. После полуночи в Казахстане
    // перечитываем вкладку, чтобы записи каждого нового дня исчезали вовремя.
    const kzClock = currentKzClock();
    const minutesUntilMidnight = 24 * 60 - kzClock.minutes;
    completedArchiveRefreshTimer = setTimeout(() => {
        completedArchiveRefreshTimer = null;
        const activePage = document.querySelector('.page.active')?.id;
        if (activePage === 'page-bookings' && currentBookingTab === 'completed') {
            loadBookings().catch(error => console.error('Не удалось обновить завершённые записи после архивирования', error));
        }
    }, minutesUntilMidnight * 60 * 1000 + 1000);
}

function paymentSummary(booking) {
    if (booking.package_id) {
        return '<span class="payment-method payment-method--package" title="Оплачено пакетом, деньги не брать">📦 Пакет</span>';
    }
    if (booking.certificate_id || booking.certificate_amount > 0) {
        return '<span class="payment-method payment-method--certificate" title="Оплачено сертификатом, деньги не брать">🎟️ Сертификат</span>';
    }
    if (booking.referral_discount_amount > 0) {
        return `<strong>🎁 Скидка: ${booking.referral_discount_amount.toLocaleString()} ₸</strong><br><strong>К оплате: ${booking.price.toLocaleString()} ₸</strong>`;
    }
    return `<strong>${booking.price.toLocaleString()} ₸</strong>`;
}

async function editBooking(bookingId) {
    try {
        const bookings = await apiGet('/bookings');
        const booking = bookings.find(b => b.id === bookingId);
        if (!booking) {
            showToast('Запись не найдена', 'error');
            return;
        }

        const instructors = await apiGet('/instructors');

        document.getElementById('modal-title').textContent = 'Изменить запись';
        document.getElementById('modal-body').innerHTML = `
            <div class="form-group">
                <label>Дата</label>
                <input type="date" id="edit-booking-date" value="${booking.date}">
            </div>
            <div class="form-group">
                <label>Время начала</label>
                <input type="time" id="edit-booking-time" value="${booking.start_time.slice(0,5)}">
            </div>
            <div class="form-group">
                <label>Инструктор</label>
                <select id="edit-booking-instructor">
                    ${instructors.map(i => `<option value="${i.id}" ${i.name === booking.instructor_name ? 'selected' : ''}>${escapeHtml(i.name)}</option>`).join('')}
                </select>
            </div>
            <div class="form-group">
                <label>КПП</label>
                <select id="edit-booking-transmission">
                    <option value="manual" ${booking.transmission === 'manual' ? 'selected' : ''}>Механика</option>
                    <option value="automatic" ${booking.transmission === 'automatic' ? 'selected' : ''}>Автомат</option>
                </select>
            </div>
            <div class="form-group">
                <label>Площадка</label>
                <select id="edit-booking-location">
                    <option value="Циолковского 30" ${booking.location === 'Циолковского 30' ? 'selected' : ''}>Циолковского 30</option>
                </select>
            </div>
            ${booking.certificate_id ? `
                <div class="form-group">
                    <label>Сертификат</label>
                    <div style="padding:8px 12px;background:var(--success-bg);color:var(--success);border-radius:6px;font-size:13px">
                        ✓ Сертификат применен (${booking.certificate_amount}₸)
                    </div>
                </div>
            ` : `
                <div class="form-group">
                    <label>Код сертификата</label>
                    <div style="display:flex;gap:8px;align-items:stretch">
                        <input type="text" id="edit-booking-certificate" placeholder="Введите код сертификата" style="flex:1">
                        <button class="btn btn-primary" onclick="applyCertificate(${bookingId})" style="white-space:nowrap;padding:0 16px">Применить</button>
                    </div>
                    <small style="color:var(--text-secondary);font-size:12px;margin-top:4px;display:block">Сертификат должен точно совпадать с ценой услуги (${booking.price}₸)</small>
                </div>
            `}
            <p style="color:var(--text-secondary);font-size:13px">⚠️ Изменения проверяются на доступность инструктора.</p>
        `;
        document.getElementById('modal-save-btn').style.display = 'block';
        document.getElementById('modal-save-btn').onclick = async () => {
            const newDate = document.getElementById('edit-booking-date').value;
            const newTime = document.getElementById('edit-booking-time').value;
            const newInstructorId = parseInt(document.getElementById('edit-booking-instructor').value);
            const newTransmission = document.getElementById('edit-booking-transmission').value;
            const newLocation = document.getElementById('edit-booking-location').value;

            if (!newDate || !newTime) {
                showToast('Заполните дату и время', 'error');
                return;
            }

            try {
                await apiPut(`/bookings/${bookingId}/edit`, {
                    new_date: newDate,
                    new_start_time: newTime,
                    new_instructor_id: newInstructorId,
                    new_transmission: newTransmission,
                    new_location: newLocation,
                });
                showToast('Запись обновлена');
                closeModal();
                loadBookings();
            } catch (e) {
                showToast(e.message, 'error');
            }
        };
        document.getElementById('app-modal').classList.remove('hidden');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function applyCertificate(bookingId) {
    const certificateCode = document.getElementById('edit-booking-certificate').value.trim();
    
    if (!certificateCode) {
        showToast('Введите код сертификата', 'error');
        return;
    }
    
    if (!confirm(`Применить сертификат ${certificateCode} к этой записи?\n\nСумма сертификата должна точно совпадать с ценой услуги.`)) {
        return;
    }
    
    try {
        const result = await apiPost(`/bookings/${bookingId}/apply-certificate`, {
            certificate_code: certificateCode
        });

        if (result && result.ok) {
            showToast(`Сертификат применен! Оплачено: ${result.amount}₸`);
            closeModal();
            loadBookings();
        } else {
            showToast('Ошибка при применении сертификата', 'error');
        }
    } catch (e) {
        showToast(e.message || 'Ошибка при применении сертификата', 'error');
    }
}

const pendingDeletes = {};

async function deleteBooking(id) {
    if (pendingDeletes[id]) {
        clearTimeout(pendingDeletes[id]);
        delete pendingDeletes[id];

        try {
            if (String(id).startsWith('offline-')) {
                await cancelUnsyncedOfflineBooking(String(id).slice('offline-'.length));
                showToast('Офлайн-запись отменена');
                loadBookings();
                return;
            }
            await apiPut(`/bookings/${id}/status`, { status: 'cancelled' });
            showToast('Запись отменена');
            loadBookings();
        } catch (e) {
            showToast(e.message, 'error');
        }
    } else {
        if (!confirm('Отменить эту запись? Нажмите OK, затем нажмите "Отменить" ещё раз для подтверждения.')) return;

        const timeoutId = setTimeout(() => {
            delete pendingDeletes[id];
            showToast('Время на подтверждение истекло', 'error');
            loadBookings();
        }, 10000);
        
        pendingDeletes[id] = timeoutId;
        showToast('Нажмите "Отменить" ещё раз в течение 10 секунд', 'error');
        loadBookings();
    }
}

async function confirmCancellation(id) {
    if (!confirm('Подтвердить отмену записи? Клиент и инструктор получат уведомление.')) return;
    try { await apiPut(`/bookings/${id}/status`, { status: 'cancelled' }); showToast('Отмена подтверждена'); loadBookings(); }
    catch (e) { showToast(e.message || 'Не удалось подтвердить отмену', 'error'); }
}

async function rejectCancellation(id) {
    if (!confirm('Отклонить заявку на отмену и сохранить запись?')) return;
    try { await apiPut(`/bookings/${id}/status`, { status: 'confirmed' }); showToast('Заявка на отмену отклонена'); loadBookings(); }
    catch (e) { showToast(e.message || 'Не удалось отклонить отмену', 'error'); }
}

async function resolveRescheduleRequest(id, action) {
    const label = action === 'confirm' ? 'подтвердить перенос' : 'отклонить заявку на перенос';
    if (!confirm(`Вы уверены, что хотите ${label}?`)) return;
    try {
        await apiPost(`/bookings/${id}/reschedule-request/resolve`, { action });
        showToast(action === 'confirm' ? 'Перенос подтверждён' : 'Заявка на перенос отклонена');
        loadBookings();
    } catch (e) {
        showToast(e.message || 'Не удалось рассмотреть заявку на перенос', 'error');
    }
}

async function purgeCancelledBooking(id) {
    if (!confirm('Удалить отменённую запись из списка без возможности восстановления?')) return;
    try {
        await apiDelete(`/bookings/${id}`);
        showToast('Отменённая запись удалена из списка');
        loadBookings();
    } catch (e) { showToast(e.message || 'Не удалось удалить запись', 'error'); }
}

async function purgeAllCancelledBookings() {
    if (currentBookingTab !== 'cancelled') return;
    if (isAdminOffline) {
        showToast('Массовое удаление отменённых записей доступно только при подключении к серверу.', 'error');
        return;
    }
    if (!confirm('Удалить все отменённые записи без возможности восстановления? Будут затронуты только записи из этой вкладки.')) return;
    try {
        const result = await apiDelete('/bookings/cancelled');
        showToast(result.deleted ? `Удалено отменённых записей: ${result.deleted}` : 'Во вкладке нет записей для удаления');
        await loadBookings();
    } catch (e) { showToast(e.message || 'Не удалось удалить отменённые записи', 'error'); }
}

async function openClientChat(clientId) {
    if (!clientId) {
        showToast('У этого клиента нет Telegram-профиля: свяжитесь с ним по телефону', 'error');
        return;
    }
    currentSupportChannel = 'clients';
    await navigateTo('support');
    await openDialog(clientId);
}

async function cancelUnsyncedOfflineBooking(operationId) {
    const operation = await offlineRead('operations', operationId);
    await removeOfflineOperation(operationId);
    const snapshot = await offlineRead('api-cache', 'offline-snapshot');
    if (!snapshot) return;
    const localBooking = (snapshot.bookings || []).find(b => String(b.id) === `offline-${operationId}`);
    snapshot.bookings = (snapshot.bookings || []).filter(b => String(b.id) !== `offline-${operationId}`);
    const client = snapshot.clients?.find(item => String(item.id) === String(localBooking?.client_id));
    if (client) client.bookings_count = Math.max(0, Number(client.bookings_count || 0) - 1);
    if (client && Number(client.id) < 0 && client.bookings_count === 0 && operation?.local_client_id === client.id) {
        snapshot.clients = snapshot.clients.filter(item => item.id !== client.id);
    }
    await offlineStore('api-cache', 'offline-snapshot', snapshot);
    await offlineStore('api-cache', '/bookings', snapshot.bookings);
    await offlineStore('api-cache', '/clients', snapshot.clients);
    renderOfflineIssues(await offlineOperations());
}

async function exportBookings() {
    if (offlineReplayInProgress) await waitForOfflineReplay();
    if (isAdminOffline) { showToast('Экспорт доступен после восстановления интернета', 'error'); return; }
    const params = new URLSearchParams();
    const df = document.getElementById('filter-date-from')?.value;
    const dt = document.getElementById('filter-date-to')?.value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    window.open(`${API}/export/bookings?${params}`, '_blank');
}

async function exportClients() {
    if (offlineReplayInProgress) await waitForOfflineReplay();
    if (isAdminOffline) { showToast('Экспорт доступен после восстановления интернета', 'error'); return; }
    window.open(`${API}/export/clients`, '_blank');
}

function exportFullBackup() { 
    if (!confirm('Создать полную резервную копию базы данных?\n\nБудут выгружены все данные: клиенты, записи, инструкторы, автопарк, сертификаты, уведомления и т.д.')) return;
    showToast('Создание резервной копии... Это может занять несколько секунд', 'success');
    window.open(`${API}/export/full-backup`, '_blank'); 
}

function showRestoreBackupDialog() {
    if (!confirm('⚠️ ВНИМАНИЕ!\n\nВосстановление из резервной копии ЗАМЕНИТ все текущие данные в базе.\n\nЭто действие необратимо!\n\nРекомендуется создать резервную копию текущего состояния перед восстановлением.\n\nПродолжить?')) {
        return;
    }
    
    // Открываем диалог выбора файла
    document.getElementById('restore-backup-file').click();
}

async function handleRestoreFile(event) {
    const file = event.target.files[0];
    if (!file) return;
    
    // Проверяем формат файла
    if (!file.name.endsWith('.html') && !file.name.endsWith('.json')) {
        showToast('Неподдерживаемый формат файла. Используйте .html или .json', 'error');
        return;
    }
    
    try {
        showToast('Чтение файла резервной копии...', 'success');
        
        const text = await file.text();
        let backupData;
        
        if (file.name.endsWith('.html')) {
            // Извлекаем JSON из HTML
            const match = text.match(/const backupData = ({[\s\S]*?});/);
            if (!match) {
                showToast('Не удалось найти данные в HTML файле', 'error');
                return;
            }
            backupData = JSON.parse(match[1]);
        } else {
            // Прямой JSON
            backupData = JSON.parse(text);
        }
        
        // Проверяем структуру данных
        if (!backupData.backup_date) {
            showToast('Неверная структура файла резервной копии', 'error');
            return;
        }
        
        // Показываем информацию о резервной копии
        const backupDate = new Date(backupData.backup_date).toLocaleString('ru-RU');
        const backupBy = backupData.backup_by || 'Неизвестно';
        
        const stats = [
            `Клиентов: ${backupData.clients?.length || 0}`,
            `Записей: ${backupData.bookings?.length || 0}`,
            `Инструкторов: ${backupData.instructors?.length || 0}`,
            `Машин: ${backupData.vehicles?.length || 0}`,
            `Сертификатов: ${backupData.certificates?.length || 0}`,
        ].join('\n');
        
        if (!confirm(`Информация о резервной копии:\n\nДата создания: ${backupDate}\nСоздал: ${backupBy}\n\n${stats}\n\n⚠️ ВСЕ ТЕКУЩИЕ ДАННЫЕ БУДУТ УДАЛЕНЫ И ЗАМЕНЕН�Это действие необратимо!\n\nПродолжить?`)) {
            return;
        }
        
        const result = await apiPost('/import/full-backup', backupData);
        if (result && result.ok) {
            showToast('Резервная копия успешно восстановлена! Страница перезагрузится.', 'success');
            setTimeout(() => location.reload(), 2000);
        } else {
            showToast('Ошибка при восстановлении резервной копии', 'error');
        }
    } catch (e) {
        showToast(e.message || 'Ошибка загрузки файла резервной копии', 'error');
    }
}
async function loadInstructors() {
    const list = document.getElementById('instructors-list');
    try {
    const data = await apiGet('/instructors');
    if (!data) {
        if (list) list.innerHTML = '<p style="color:var(--danger);text-align:center;padding:40px">Не удалось загрузить инструкторов. Обновите страницу.</p>';
        return;
    }
    const transLabels = { manual: '\u041c\u0435\u0445\u0430\u043d\u0438\u043a\u0430', automatic: '\u0410\u0432\u0442\u043e\u043c\u0430\u0442', both: '\u041c\u0435\u0445. \u0438 \u0430\u0432\u0442.' };
    const lessonLabels = { training: 'Вождение', exam: 'Пробный экзамен', both: 'Вождение и экзамен' };
    if (!data.length) {
        list.innerHTML = '<p style="color:var(--text-secondary);text-align:center;padding:40px">\u041d\u0435\u0442 \u0438\u043d\u0441\u0442\u0440\u0443\u043a\u0442\u043e\u0440\u043e\u0432</p>';
        return;
    }
    list.innerHTML = data.map(i => {
        const isActive = i.is_active !== false;
        const initials = i.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
        const toggleLabel = isActive ? '\u0414\u0435\u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u0442\u044c' : '\u0410\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u0442\u044c';
        const toggleClass = isActive ? 'btn-outline-danger' : 'btn-success';
        const dutyBadge = i.is_duty
            ? '<span class="badge badge-warning" style="margin-left:6px;font-size:0.68rem">\ud83d\udfe1 \u0414\u0435\u0436\u0443\u0440\u043d\u044b\u0439</span>'
            : '';
        const statusHtml = isActive
            ? '<span class="inst-status-badge inst-status-active">\u25cf \u0410\u043a\u0442\u0438\u0432\u0435\u043d</span>'
            : '<span class="inst-status-badge inst-status-inactive">\u25cf \u041d\u0435\u0430\u043a\u0442\u0438\u0432\u0435\u043d</span>';
        const rating = Number(i.rating);
        const ratingHtml = `<span class="inst-rating-badge">\u2605 ${Number.isFinite(rating) ? rating.toFixed(1) : '5.0'}</span>`;
        const descHtml = i.description
            ? `<div class="inst-quote">\u00ab${escapeHtml(i.description)}\u00bb</div>`
            : '';
        const safeName = i.name.replace(/'/g, "\\'");
        return `
        <div class="inst-card${i.is_duty ? ' inst-card-duty' : ''}">
            <div class="inst-header">
                <div class="inst-profile">
                    <div class="inst-avatar">${initials}</div>
                    <div class="inst-name-block">
                        <span class="inst-name">${escapeHtml(i.name)}${dutyBadge}</span>
                        ${statusHtml}
                    </div>
                </div>
                ${ratingHtml}
            </div>
            <div class="inst-specs-grid">
                <div class="inst-spec-item">📋 ${lessonLabels[i.lesson_type] || lessonLabels.both}</div>
                <div class="inst-spec-item">\ud83d\ude97 ${transLabels[i.transmission] || i.transmission}</div>
                <div class="inst-spec-item">\ud83c\udf93 \u0421\u0442\u0430\u0436 ${i.experience_years} \u043b.</div>
                <div class="inst-spec-item">\ud83d\udd50 ${(i.working_hours_start || '09:00').slice(0,5)}\u2013${(i.working_hours_end || '19:00').slice(0,5)}</div>
                <div class="inst-spec-item">\ud83d\udcac ${i.telegram_username ? '@' + i.telegram_username : (i.telegram_id || '\u2014')}</div>
            </div>
            ${descHtml}
            <div class="inst-actions-toolbar">
                <button class="btn btn-outline btn-sm" onclick="editInstructor(${i.id})">\u270f\ufe0f \u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c</button>
                <button class="btn btn-outline btn-sm" onclick="showInstructorWeek(${i.id})">\ud83d\udcc5 \u041d\u0435\u0434\u0435\u043b\u044f</button>
                <button class="btn ${toggleClass} btn-sm" onclick="toggleInstructorActive(${i.id}, ${!isActive})">${toggleLabel}</button>
                <button class="btn btn-icon btn-outline-danger btn-sm" onclick="deleteInstructor(${i.id}, '${safeName}')" title="\u0423\u0434\u0430\u043b\u0438\u0442\u044c">\ud83d\uddd1\ufe0f</button>
            </div>
        </div>`;
    }).join('');
    } catch (e) {
        console.error('Не удалось загрузить инструкторов', e);
        if (list) list.innerHTML = `<p style="color:var(--danger);text-align:center;padding:40px">Ошибка загрузки: ${escapeHtml(e.message || 'проверьте соединение с сервером')}</p>`;
    }
}

function addKzDays(dateString, offset) {
    const [year, month, day] = dateString.split('-').map(Number);
    const value = new Date(Date.UTC(year, month - 1, day + offset));
    return value.toISOString().slice(0, 10);
}

async function buildOfflineInstructorWeek(instructorId) {
    const snapshot = await offlineRead('api-cache', 'offline-snapshot');
    const instructor = snapshot?.instructors?.find(item => String(item.id) === String(instructorId));
    if (!snapshot || !instructor) {
        throw new Error('Нет локальной копии инструктора и его расписания. Сначала откройте админку при наличии интернета.');
    }
    const startDate = currentKzClock().date;
    const dates = Array.from({ length: 7 }, (_, offset) => addKzDays(startDate, offset));
    const days = new Map(dates.map(day => [day, []]));
    const addBooking = (booking, source = booking.source || 'telegram') => {
        const activeStatuses = source === 'mobile'
            ? ['pending', 'cancellation_pending', 'reschedule_pending', 'planned', 'confirmed']
            : ['pending', 'cancellation_pending', 'reschedule_pending', 'planned', 'confirmed', 'in_progress'];
        if (String(booking.instructor_id) !== String(instructorId) || !days.has(booking.date) || !activeStatuses.includes(booking.status)) return;
        days.get(booking.date).push({
            id: booking.id, source,
            client_name: booking.client_name || '—', client_phone: booking.client_phone || '',
            service_type: booking.service_type, transmission: booking.transmission, location: booking.location,
            start_time: String(booking.start_time || '').slice(0, 5),
            end_time: String(booking.end_time || '').slice(0, 5),
            status: booking.status, price: booking.price,
        });
    };
    (snapshot.bookings || []).forEach(booking => addBooking(booking));
    (snapshot.data?.['/offline-mobile-bookings'] || []).forEach(booking => addBooking(booking, 'mobile'));
    return {
        instructor: { id: instructor.id, name: instructor.name },
        start_date: startDate, end_date: dates[6],
        days: dates.map(date => ({
            date,
            bookings: days.get(date).sort((a, b) => a.start_time.localeCompare(b.start_time)),
        })),
        offline: true,
    };
}

async function showInstructorWeek(instructorId) {
    const panel = document.getElementById('instructor-week-panel');
    const title = document.getElementById('week-panel-title');
    const body = document.getElementById('week-panel-body');
    panel.classList.remove('hidden');
    body.innerHTML = '<div class="week-empty">Загрузка...</div>';
    let data;
    try {
        data = isAdminOffline
            ? await buildOfflineInstructorWeek(instructorId)
            : await apiGet(`/instructors/${instructorId}/week-bookings`);
    } catch (error) {
        if (!isAdminOffline) throw error;
        data = await buildOfflineInstructorWeek(instructorId);
    }
    if (!data) {
        body.innerHTML = '<div class="week-empty">Не удалось загрузить занятость</div>';
        return;
    }
    title.textContent = data.instructor.name;
    const serviceLabels = {training: 'Вождение', exam: 'Пробный экзамен'};
    const transLabels = {manual: 'Механика', automatic: 'Автомат', both: 'Обе'};
    const statusLabels = {planned: 'Запланирована', confirmed: 'Подтверждена', in_progress: 'В процессе'};
    body.innerHTML = data.days.map(day => {
        const dayTitle = formatWeekDate(day.date);
        if (!day.bookings.length) {
            return `<section class="week-day"><h4>${dayTitle}</h4><div class="week-empty">Свободно</div></section>`;
        }
        return `<section class="week-day">
            <h4>${dayTitle}</h4>
            ${day.bookings.map(b => `
                <div class="week-booking">
                    <div class="week-booking-time">${b.start_time}–${b.end_time}</div>
                    <div class="week-booking-main">
                        <strong>${escapeHtml(b.client_name || '—')}</strong>
                        <span>${escapeHtml(b.client_phone || 'телефон не указан')}</span>
                        <span>${serviceLabels[b.service_type] || b.service_type} · ${transLabels[b.transmission] || b.transmission} · ${statusLabels[b.status] || b.status}</span>
                    </div>
                </div>
            `).join('')}
        </section>`;
    }).join('');
}

function closeInstructorWeekPanel() {
    document.getElementById('instructor-week-panel')?.classList.add('hidden');
}

function formatWeekDate(dateStr) {
    const names = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
    const date = new Date(dateStr + 'T00:00:00');
    const day = String(date.getDate()).padStart(2, '0');
    const month = String(date.getMonth() + 1).padStart(2, '0');
    return `${names[date.getDay()]}, ${day}.${month}`;
}

// State for the instructor editor. These declarations existed in the working
// admin before the redesign; without them the very first click on "Добавить
// инструктора" throws ReferenceError and the form cannot open.
let editingInstructorId = null;
const selectedDaysOff = new Set();
const instructorDailySchedules = new Map();
let currentDailyScheduleMode = 'default';

function showInstructorForm() {
    editingInstructorId = null;
    selectedDaysOff.clear();
    instructorDailySchedules.clear();
    
    document.getElementById('instructor-form-title').textContent = 'Новый инструктор';
    document.getElementById('inst-name').value = '';
    document.getElementById('inst-phone').value = '';
    document.getElementById('inst-tg-id').value = '';
    document.getElementById('inst-tg-user').value = '';
    document.getElementById('inst-trans').value = 'both';
    document.getElementById('inst-lesson-type').value = 'both';
    document.getElementById('inst-gender').value = 'any';
    document.getElementById('inst-exp').value = '0';
    document.getElementById('inst-start').value = '09:00';
    document.getElementById('inst-end').value = '19:00';
    document.getElementById('inst-lunch-start').value = '';
    document.getElementById('inst-lunch-end').value = '';
    document.getElementById('inst-desc').value = '';
    document.getElementById('inst-duty').checked = false;
    document.getElementById('inst-lead').checked = false;
    clearDailyScheduleForm();
    
    renderCalendar();
    const form = document.getElementById('instructor-form');
    form.classList.remove('hidden');
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function hideInstructorForm() {
    document.getElementById('instructor-form').classList.add('hidden');
    editingInstructorId = null;
}

async function editInstructor(id) {
    try {
        const data = await apiGet('/instructors');
        if (!Array.isArray(data)) throw new Error('Не удалось загрузить список инструкторов');
        const i = data.find(x => x.id === id);
        if (!i) throw new Error('Инструктор не найден');

        editingInstructorId = id;
        selectedDaysOff.clear();
        instructorDailySchedules.clear();

        document.getElementById('instructor-form-title').textContent = 'Редактировать инструктора';
        document.getElementById('inst-name').value = i.name;
        document.getElementById('inst-phone').value = i.phone || '';
        document.getElementById('inst-tg-id').value = i.telegram_id || '';
        document.getElementById('inst-tg-user').value = i.telegram_username || '';
        document.getElementById('inst-trans').value = i.transmission;
        document.getElementById('inst-lesson-type').value = i.lesson_type || 'both';
        document.getElementById('inst-gender').value = i.gender || 'any';
        document.getElementById('inst-exp').value = i.experience_years;
        document.getElementById('inst-start').value = (i.working_hours_start || '09:00').slice(0,5);
        document.getElementById('inst-end').value = (i.working_hours_end || '19:00').slice(0,5);
        document.getElementById('inst-lunch-start').value = (i.lunch_start || '').slice(0,5);
        document.getElementById('inst-lunch-end').value = (i.lunch_end || '').slice(0,5);
        document.getElementById('inst-desc').value = i.description || '';
        document.getElementById('inst-duty').checked = i.is_duty || false;
        document.getElementById('inst-lead').checked = i.is_lead || false;

        // График подгружаем дополнительно: сама форма редактирования должна
        // открыться даже если старые данные графика нечитаемы.
        const [daysOff, schedules] = await Promise.all([
            apiGet(`/instructors/${id}/days-off`).catch((e) => {
                console.warn('Не удалось загрузить выходные инструктора', e);
                return [];
            }),
            apiGet(`/instructors/${id}/daily-schedules`).catch((e) => {
                console.warn('Не удалось загрузить дневной график инструктора', e);
                return [];
            }),
        ]);
        if (Array.isArray(daysOff)) daysOff.forEach(d => selectedDaysOff.add(d.date));
        if (Array.isArray(schedules)) schedules.forEach(s => instructorDailySchedules.set(s.schedule_date, s));
        clearDailyScheduleForm();

        renderCalendar();
        const form = document.getElementById('instructor-form');
        form.classList.remove('hidden');
        form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (e) {
        console.error('Не удалось открыть форму редактирования инструктора', e);
        showToast(e.message || 'Не удалось открыть инструктора', 'error');
    }
}

function clearDailyScheduleForm() {
    ['daily-schedule-date', 'daily-work-start', 'daily-work-end', 'daily-lunch-start', 'daily-lunch-end'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = '';
    });
    document.getElementById('daily-schedule-title').textContent = '—';
    document.getElementById('daily-schedule-empty').classList.remove('hidden');
    document.getElementById('daily-schedule-panel').classList.add('hidden');
    setDailyScheduleMode('default');
}

function setDailyScheduleMode(mode) {
    currentDailyScheduleMode = mode;
    ['default', 'off', 'custom'].forEach(name => {
        const btn = document.getElementById(`schedule-mode-${name}`);
        if (btn) btn.classList.toggle('active', name === mode);
    });
    document.getElementById('daily-custom-fields')?.classList.toggle('hidden', mode !== 'custom');
}

async function saveDailySchedule() {
    if (!editingInstructorId) {
        showToast('Сначала сохраните инструктора', 'error');
        return;
    }
    const scheduleDate = document.getElementById('daily-schedule-date').value;
    if (!scheduleDate) {
        showToast('Выберите дату', 'error');
        return;
    }
    if (currentDailyScheduleMode === 'default') {
        await apiDelete(`/instructors/${editingInstructorId}/daily-schedules/${scheduleDate}`);
        instructorDailySchedules.delete(scheduleDate);
        selectedDaysOff.delete(scheduleDate);
        showToast('На дату вернули обычный график');
        renderCalendar();
        fillDailyScheduleForm(scheduleDate);
        return;
    }
    const payload = {
        schedule_date: scheduleDate,
        is_day_off: currentDailyScheduleMode === 'off',
        working_hours_start: currentDailyScheduleMode === 'custom' ? (document.getElementById('daily-work-start').value || null) : null,
        working_hours_end: currentDailyScheduleMode === 'custom' ? (document.getElementById('daily-work-end').value || null) : null,
        lunch_start: currentDailyScheduleMode === 'custom' ? (document.getElementById('daily-lunch-start').value || null) : null,
        lunch_end: currentDailyScheduleMode === 'custom' ? (document.getElementById('daily-lunch-end').value || null) : null,
    };
    if (currentDailyScheduleMode === 'custom' && (!payload.working_hours_start || !payload.working_hours_end)) {
        showToast('Укажите время начала и конца работы', 'error');
        return;
    }
    await apiPut(`/instructors/${editingInstructorId}/daily-schedules`, payload);
    instructorDailySchedules.set(scheduleDate, payload);
    if (payload.is_day_off) selectedDaysOff.add(scheduleDate);
    else selectedDaysOff.delete(scheduleDate);
    showToast('Расписание дня сохранено');
    renderCalendar();
    fillDailyScheduleForm(scheduleDate);
}

async function toggleInstructorActive(id, active) {
    await apiPut(`/instructors/${id}`, {is_active: active});
    showToast(active ? 'Инструктор активирован' : 'Инструктор деактивирован');
    loadInstructors();
}

async function deleteInstructor(id, name) {
    if (!confirm(`Удалить инструктора "${name}"?\n\nУдаление недоступно только при наличии активных записей. Завершённые записи и история останутся.`)) return;
    try {
        await apiDelete(`/instructors/${id}`);
        showToast('Инструктор удалён');
        loadInstructors();
    } catch (e) {
        showToast(e.message || 'Ошибка удаления', 'error');
    }
}

// --- Fleet ---
let fleetVehicles = [];

function vehicleTransmissionLabel(transmission) {
    return transmission === 'manual' ? 'МКПП' : 'АКПП';
}

async function loadVehicles() {
    const list = document.getElementById('vehicles-list');
    if (!list) return;
    const data = await apiGet('/vehicles');
    if (!data) return;
    fleetVehicles = data;
    if (!fleetVehicles.length) {
        list.innerHTML = '<div class="empty-state">В автопарке пока нет машин. Добавьте первую карточку.</div>';
        return;
    }
    list.innerHTML = fleetVehicles.map(vehicle => `
        <article class="fleet-card" id="vehicle-card-${vehicle.id}">
            <div class="fleet-card-header"><div><span class="fleet-icon">🚗</span><strong>${escapeHtml(vehicle.name)}</strong></div><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end;"><span class="fleet-transmission fleet-${vehicle.transmission}">${vehicleTransmissionLabel(vehicle.transmission)}</span>${vehicle.is_under_repair ? '<span class="badge badge-cancelled">На ремонте</span>' : ''}</div></div>
            <div class="fleet-card-actions"><button class="btn btn-outline btn-sm" onclick="editVehicle(${vehicle.id})">Редактировать</button><button class="btn ${vehicle.is_under_repair ? 'btn-primary' : 'btn-outline-danger'} btn-sm" onclick="toggleVehicleRepair(${vehicle.id}, ${!vehicle.is_under_repair})">${vehicle.is_under_repair ? 'Вернуть в строй' : 'На ремонте'}</button><button class="btn btn-outline-danger btn-sm" onclick="deleteVehicle(${vehicle.id})">Удалить</button></div>
            <div class="fleet-edit-panel hidden" id="vehicle-edit-${vehicle.id}">
                <div class="form-group"><label>Название машины</label><input id="vehicle-name-${vehicle.id}" value="${escapeHtml(vehicle.name)}" maxlength="100"></div>
                <div class="form-group"><label>Тип КПП</label><select id="vehicle-transmission-${vehicle.id}"><option value="manual" ${vehicle.transmission === 'manual' ? 'selected' : ''}>Механика (МКПП)</option><option value="automatic" ${vehicle.transmission === 'automatic' ? 'selected' : ''}>Автомат (АКПП)</option></select></div>
                <div class="fleet-edit-actions"><button class="btn btn-primary btn-sm" onclick="saveVehicle(${vehicle.id})">Сохранить</button><button class="btn btn-outline btn-sm" onclick="editVehicle(${vehicle.id})">Отмена</button></div>
            </div>
        </article>
    `).join('');
}

function editVehicle(id) { document.getElementById(`vehicle-edit-${id}`)?.classList.toggle('hidden'); }

async function saveVehicle(id) {
    const name = document.getElementById(`vehicle-name-${id}`)?.value.trim();
    const transmission = document.getElementById(`vehicle-transmission-${id}`)?.value;
    if (!name) return showToast('Укажите название машины', 'error');
    try { await apiPut(`/vehicles/${id}`, { name, transmission }); showToast('Карточка машины обновлена'); await loadVehicles(); }
    catch (error) { showToast(error.message || 'Не удалось сохранить машину', 'error'); }
}

async function addVehicle() {
    if (fleetVehicles.length >= 6) return showToast('В автопарке может быть не более 6 машин', 'error');
    const name = prompt('Название машины', `Машина ${fleetVehicles.length + 1}`);
    if (name === null) return;
    if (!name.trim()) return showToast('Укажите название машины', 'error');
    const transmission = confirm('Это машина с МКПП?\n\n«ОК» — МКПП, «Отмена» — АКПП.') ? 'manual' : 'automatic';
    try { await apiPost('/vehicles', { name: name.trim(), transmission }); showToast('Машина добавлена'); await loadVehicles(); }
    catch (error) { showToast(error.message || 'Не удалось добавить машину', 'error'); }
}

async function deleteVehicle(id) {
    const vehicle = fleetVehicles.find(item => item.id === id);
    if (!confirm(`Удалить карточку «${vehicle?.name || 'машины'}»? Исторические записи сохранятся без привязки к машине.`)) return;
    try { await apiDelete(`/vehicles/${id}`); showToast('Машина удалена'); await loadVehicles(); }
    catch (error) { showToast(error.message || 'Нельзя удалить машину с активными записями', 'error'); }
}

async function toggleVehicleRepair(id, isUnderRepair) {
    const vehicle = fleetVehicles.find(item => item.id === id);
    const action = isUnderRepair ? 'поставить на ремонт' : 'вернуть в строй';
    if (!confirm(`${action[0].toUpperCase()}${action.slice(1)} машину «${vehicle?.name || ''}»?`)) return;
    try { await apiPut(`/vehicles/${id}/repair`, { is_under_repair: isUnderRepair }); showToast(isUnderRepair ? 'Машина снята со свободных слотов' : 'Машина возвращена в доступные слоты'); await loadVehicles(); }
    catch (error) { showToast(error.message || 'Не удалось изменить статус машины', 'error'); }
}

async function saveInstructor() {
    // Empty strings must be preserved on update so optional values can be cleared.
    const lunchStart = document.getElementById('inst-lunch-start').value;
    const lunchEnd = document.getElementById('inst-lunch-end').value;
    const payload = {
        name: document.getElementById('inst-name').value,
        phone: document.getElementById('inst-phone').value.trim(),
        telegram_id: document.getElementById('inst-tg-id').value.trim(),
        // Пустая строка очищает поле; @ добавляется только при отображении.
        telegram_username: document.getElementById('inst-tg-user').value.trim().replace(/^@+/, ''),
        transmission: document.getElementById('inst-trans').value,
        lesson_type: document.getElementById('inst-lesson-type').value,
        gender: document.getElementById('inst-gender').value,
        experience_years: parseInt(document.getElementById('inst-exp').value) || 0,
        working_hours_start: document.getElementById('inst-start').value,
        working_hours_end: document.getElementById('inst-end').value,
        lunch_start: lunchStart,
        lunch_end: lunchEnd,
        days_off: '', // Оставляем пустым, теперь используем отдельную таблицу
        description: document.getElementById('inst-desc').value.trim(),
        is_duty: document.getElementById('inst-duty').checked,
        is_lead: document.getElementById('inst-lead').checked,
    };
    
    if (!payload.name.trim()) {
        showToast('Укажите ФИО инструктора', 'error');
        return;
    }
    try {
        let instructorId = editingInstructorId;
        if (editingInstructorId) {
            await apiPut(`/instructors/${editingInstructorId}`, payload);
            showToast('Инструктор обновлён');
        } else {
            const result = await apiPost('/instructors', payload);
            instructorId = result?.id;
            if (!instructorId) throw new Error('Сервер не вернул идентификатор инструктора');
            showToast('Инструктор добавлен');
        }
        await apiPut(`/instructors/${instructorId}/days-off`, {
            days_off_dates: Array.from(selectedDaysOff)
        });
        hideInstructorForm();
        loadInstructors();
    } catch (e) {
        showToast(e.message || 'Не удалось сохранить инструктора', 'error');
    }
}

// --- Analytics ---
let analyticsRevenueData = null;
let analyticsRevenuePeriod = 'all';
let analyticsRevenueRefreshTimer = null;
const ANALYTICS_REVENUE_REFRESH_MS = 3 * 60 * 60 * 1000;

function formatRevenueAmount(value) {
    return `${Number(value || 0).toLocaleString('ru-RU')} ₸`;
}

function formatRevenueAxis(value) {
    const amount = Number(value || 0);
    if (amount >= 1000000) return `${(amount / 1000000).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} млн`;
    if (amount >= 1000) return `${Math.round(amount / 1000).toLocaleString('ru-RU')} тыс`;
    return amount.toLocaleString('ru-RU');
}

function revenuePointLabel(timestamp, period) {
    const value = new Date(timestamp);
    if (period === 'month') {
        const nominalEnd = new Date(value.getTime() + 6 * 24 * 60 * 60 * 1000);
        const dataUpdatedAt = new Date(analyticsRevenueData?.updated_at || Date.now());
        const end = nominalEnd > dataUpdatedAt ? dataUpdatedAt : nominalEnd;
        const startLabel = value.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', timeZone: 'Asia/Almaty' });
        const endLabel = end.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', timeZone: 'Asia/Almaty' });
        return `${startLabel}–${endLabel}`;
    }
    const options = period === 'day'
        ? { hour: '2-digit', minute: '2-digit' }
        : period === 'week'
        ? { weekday: 'short', day: '2-digit', month: 'short' }
        : period === 'all'
        ? { day: '2-digit', month: 'short', year: '2-digit' }
        : { day: '2-digit', month: 'short' };
    return value.toLocaleString('ru-RU', { ...options, timeZone: 'Asia/Almaty' }).replace('.', '');
}

function renderProfitabilityList(containerId, items) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!Array.isArray(items) || !items.length) {
        container.innerHTML = '<p style="color:var(--text-muted);font-size:.78rem;">Пока нет завершённых записей</p>';
        return;
    }
    container.innerHTML = items.map((item, index) => `
        <div class="profitability-item">
          <div class="profitability-item-label"><span class="profitability-rank">${index + 1}</span>${escapeHtml(item.label || '—')}</div>
          <div class="profitability-item-value">
            <strong>${formatRevenueAmount(item.revenue)}</strong>
            <small>${Number(item.bookings || 0).toLocaleString('ru-RU')} записей</small>
          </div>
        </div>`).join('');
}

function renderRevenueAnalytics() {
    const chart = document.getElementById('chart-revenue-analytics');
    const total = document.getElementById('analytics-revenue-total');
    const updated = document.getElementById('analytics-revenue-updated');
    document.querySelectorAll('[data-revenue-period]').forEach(button => {
        button.classList.toggle('active', button.dataset.revenuePeriod === analyticsRevenuePeriod);
    });
    renderProfitabilityList('analytics-profitable-hours', analyticsRevenueData?.profitable_hours);
    renderProfitabilityList('analytics-profitable-days', analyticsRevenueData?.profitable_days);
    const periodData = analyticsRevenueData?.periods?.[analyticsRevenuePeriod];
    const points = Array.isArray(periodData?.points) ? periodData.points : [];
    if (total) total.textContent = formatRevenueAmount(periodData?.total_revenue);
    if (updated) updated.textContent = analyticsRevenueData?.updated_at
        ? `Обновлено ${new Date(analyticsRevenueData.updated_at).toLocaleString('ru-RU', { timeZone: 'Asia/Almaty' })} · шкала ${periodData?.granularity_label || 'по интервалам'} · автообновление ${Number(analyticsRevenueData?.refresh_interval_hours || 3)} ч.`
        : 'Данные автоматически обновляются каждые 3 часа';
    if (!chart) return;
    if (!points.length) {
        chart.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:90px 12px;">Пока нет данных для графика</p>';
        return;
    }
    const width = 1000;
    const height = 360;
    const padding = { top: 18, right: 82, bottom: 38, left: 18 };
    const plotWidth = width - padding.left - padding.right;
    const lineBottom = 250;
    const lineHeight = lineBottom - padding.top;
    const volumeTop = 274;
    const volumeBottom = 318;
    const revenues = points.map(point => Number(point.revenue || 0));
    const minRevenue = Math.min(...revenues);
    const maxRevenue = Math.max(...revenues);
    const spread = maxRevenue - minRevenue;
    const scalePadding = spread > 0 ? spread * .12 : Math.max(Math.abs(maxRevenue) * .15, 1000);
    const axisMin = Math.max(0, minRevenue - scalePadding);
    const axisMax = Math.max(axisMin + 1, maxRevenue + scalePadding);
    const xAt = index => points.length === 1
        ? padding.left + plotWidth / 2
        : padding.left + index * plotWidth / (points.length - 1);
    const yAt = value => padding.top + (axisMax - Number(value || 0)) * lineHeight / (axisMax - axisMin);
    const coordinates = points.map((point, index) => [xAt(index), yAt(point.revenue)]);
    const linePath = coordinates.map(([x, y], index) => `${index ? 'L' : 'M'} ${x.toFixed(2)} ${y.toFixed(2)}`).join(' ');
    const areaPath = `${linePath} L ${coordinates.at(-1)[0].toFixed(2)} ${lineBottom} L ${coordinates[0][0].toFixed(2)} ${lineBottom} Z`;
    const grid = Array.from({ length: 5 }, (_, index) => {
        const ratio = index / 4;
        const y = padding.top + lineHeight * ratio;
        const value = axisMax - (axisMax - axisMin) * ratio;
        return `<line class="analytics-chart-grid" x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}"></line><text class="analytics-chart-label" x="${width - padding.right + 10}" y="${y + 4}">${escapeHtml(formatRevenueAxis(value))}</text>`;
    }).join('');
    const labelCount = Math.min(points.length, analyticsRevenuePeriod === 'week' ? 7 : analyticsRevenuePeriod === 'month' ? 5 : 6);
    const labelIndexes = [...new Set(Array.from({ length: labelCount }, (_, index) =>
        Math.round(index * (points.length - 1) / Math.max(1, labelCount - 1))))];
    const xLabels = labelIndexes.map(index => `<line class="analytics-chart-grid analytics-chart-grid-vertical" x1="${xAt(index)}" y1="${padding.top}" x2="${xAt(index)}" y2="${volumeBottom}"></line><text class="analytics-chart-label" x="${xAt(index)}" y="${height - 12}" text-anchor="middle">${escapeHtml(revenuePointLabel(points[index].timestamp, analyticsRevenuePeriod))}</text>`).join('');
    const maxBookings = Math.max(...points.map(point => Number(point.bookings || 0)), 1);
    const barWidth = Math.max(1.5, Math.min(18, plotWidth / Math.max(points.length, 1) * .62));
    const volumeBars = points.map((point, index) => {
        const bookings = Number(point.bookings || 0);
        const barHeight = bookings ? Math.max(2, bookings * (volumeBottom - volumeTop) / maxBookings) : 1;
        const previousRevenue = index ? Number(points[index - 1].revenue || 0) : Number(point.revenue || 0);
        const direction = Number(point.revenue || 0) >= previousRevenue ? 'up' : 'down';
        const title = `${revenuePointLabel(point.timestamp, analyticsRevenuePeriod)} · ${bookings.toLocaleString('ru-RU')} завершённых записей`;
        return `<rect class="analytics-volume-bar ${direction}" x="${(xAt(index) - barWidth / 2).toFixed(2)}" y="${(volumeBottom - barHeight).toFixed(2)}" width="${barWidth.toFixed(2)}" height="${barHeight.toFixed(2)}"><title>${escapeHtml(title)}</title></rect>`;
    }).join('');
    const pointStep = Math.max(1, Math.ceil(points.length / 96));
    const markers = points.map((point, index) => {
        if (index % pointStep !== 0 && index !== points.length - 1) return '';
        const previousRevenue = index ? Number(points[index - 1].revenue || 0) : Number(point.revenue || 0);
        const direction = Number(point.revenue || 0) >= previousRevenue ? 'up' : 'down';
        const title = `${revenuePointLabel(point.timestamp, analyticsRevenuePeriod)} · выручка ${formatRevenueAmount(point.revenue)} · записей ${Number(point.bookings || 0).toLocaleString('ru-RU')}`;
        return `<circle class="analytics-chart-point ${direction}" cx="${xAt(index)}" cy="${yAt(point.revenue)}" r="4"><title>${escapeHtml(title)}</title></circle>`;
    }).join('');
    chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="График выручки за ${escapeHtml(periodData.label || '')}"><defs><linearGradient id="analyticsRevenueGradient" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#64748b" stop-opacity=".16"></stop><stop offset="100%" stop-color="#64748b" stop-opacity=".01"></stop></linearGradient></defs>${grid}${xLabels}<path class="analytics-chart-area" d="${areaPath}"></path><path class="analytics-chart-line" d="${linePath}"></path>${markers}${volumeBars}</svg>`;
}

function clearAnalyticsRevenueRefresh() {
    if (analyticsRevenueRefreshTimer) {
        clearTimeout(analyticsRevenueRefreshTimer);
        analyticsRevenueRefreshTimer = null;
    }
}

function scheduleAnalyticsRevenueRefresh() {
    clearAnalyticsRevenueRefresh();
    analyticsRevenueRefreshTimer = setTimeout(async () => {
        analyticsRevenueRefreshTimer = null;
        if (document.querySelector('.page.active')?.id !== 'page-analytics') return;
        try {
            const revenueData = await apiGet('/analytics/revenue');
            if (revenueData) {
                analyticsRevenueData = revenueData;
                renderRevenueAnalytics();
            }
        } catch (error) {
            console.error('Не удалось обновить график выручки', error);
        }
        scheduleAnalyticsRevenueRefresh();
    }, ANALYTICS_REVENUE_REFRESH_MS);
}

function setRevenuePeriod(period) {
    if (!['day', 'week', 'month', 'all'].includes(period)) return;
    analyticsRevenuePeriod = period;
    renderRevenueAnalytics();
}

async function loadAnalytics() {
    const [heatmap, load, bookings, sourceData, genderData, revenueData] = await Promise.all([
        apiGet('/analytics/heatmap'),
        apiGet('/analytics/instructor-load'),
        apiGet('/bookings').catch(() => []),
        apiGet('/analytics/booking-sources').catch(() => null),
        apiGet('/analytics/gender').catch(() => null),
        apiGet('/analytics/revenue').catch(() => null)
    ]);

    analyticsRevenueData = revenueData;
    renderRevenueAnalytics();
    scheduleAnalyticsRevenueRefresh();

    const renderSource = (key, data) => {
        const count = Number(data?.count || 0);
        const percent = Number(data?.percent || 0);
        const countEl = document.getElementById(`analytics-source-${key}`);
        const percentEl = document.getElementById(`analytics-source-${key}-pct`);
        const barEl = document.getElementById(`analytics-source-${key}-bar`);
        if (countEl) countEl.textContent = `${percent.toLocaleString('ru-RU')}%`;
        if (percentEl) percentEl.textContent = `${count.toLocaleString('ru-RU')} записей`;
        if (barEl) barEl.style.width = `${Math.min(100, Math.max(0, percent))}%`;
    };
    renderSource('telegram', sourceData?.telegram);
    renderSource('mobile', sourceData?.mobile);
    renderSource('manual', sourceData?.manual);

    const renderGender = (key, data) => {
        const ready = genderData?.status === 'ready';
        const percent = Number(data?.percent || 0);
        const percentEl = document.getElementById(`analytics-gender-${key}`);
        const countEl = document.getElementById(`analytics-gender-${key}-count`);
        const barEl = document.getElementById(`analytics-gender-${key}-bar`);
        if (percentEl) percentEl.textContent = ready ? `${percent.toLocaleString('ru-RU')}%` : '—';
        if (countEl) countEl.textContent = ready
            ? `${Number(data?.count || 0).toLocaleString('ru-RU')} клиентов`
            : 'Расчёт ещё не выполнен';
        if (barEl) barEl.style.width = ready ? `${Math.min(100, Math.max(0, percent))}%` : '0%';
    };
    renderGender('male', genderData?.male);
    renderGender('female', genderData?.female);
    renderGender('unknown', genderData?.unknown);
    const genderUpdatedEl = document.getElementById('analytics-gender-updated');
    if (genderUpdatedEl) {
        genderUpdatedEl.textContent = genderData?.status === 'ready' && genderData?.updated_at
            ? `Обновлено: ${new Date(genderData.updated_at).toLocaleString('ru-RU')}`
            : 'Ожидает первого расчёта';
    }
    
    // 2. Services Split (Training vs Exam)
    const trainEl = document.getElementById('analytics-training-pct');
    const examEl = document.getElementById('analytics-exam-pct');
    if (trainEl && examEl) {
        if (Array.isArray(bookings) && bookings.length > 0) {
            const trainingCount = bookings.filter(b => b.service_type !== 'exam').length;
            const examCount = bookings.filter(b => b.service_type === 'exam').length;
            const total = trainingCount + examCount || 1;
            const trainPct = Math.round((trainingCount / total) * 100);
            const examPct = 100 - trainPct;
            trainEl.textContent = `${trainPct}%`;
            examEl.textContent = `${examPct}%`;
        } else {
            trainEl.textContent = '85%';
            examEl.textContent = '15%';
        }
    }

    // 3. Heatmap
    const hc = document.getElementById('heatmap-container');
    if (hc) {
        if (heatmap && heatmap.length) {
            const maxCount = Math.max(...heatmap.map(h => h.count), 1);
            let html = '';
            const byDate = {};
            heatmap.forEach(h => { 
                if (!byDate[h.date]) byDate[h.date] = []; 
                byDate[h.date].push(h); 
            });
            
            const lastDays = Object.entries(byDate).slice(-7);
            if (lastDays.length > 0) {
                lastDays.forEach(([d, hours]) => {
                    const dayName = hours[0]?.day_name || d;
                    html += `<div style="display:flex;flex-direction:column;align-items:center;gap:3px;">
                        <div style="font-size:11.5px;font-weight:700;color:var(--text-main);padding:4px 0;">${escapeHtml(dayName)}</div>`;
                    for (let h = 9; h <= 20; h++) {
                        const entry = hours.find(x => x.hour === h);
                        const count = entry ? entry.count : 0;
                        const intensity = count / maxCount;
                        const bg = count === 0 
                            ? '#f1f5f9' 
                            : `rgba(37, 99, 235, ${Math.min(1, 0.25 + intensity * 0.75)})`;
                        const textColor = count === 0 ? '#94a3b8' : '#ffffff';
                        html += `<div class="heatmap-cell" style="background:${bg};color:${textColor}" title="${h}:00 — ${count} записей">${count > 0 ? count : ''}</div>`;
                    }
                    html += `</div>`;
                });
                hc.innerHTML = html;
            } else {
                hc.innerHTML = '<p style="color:var(--text-secondary);grid-column:1/-1;text-align:center;padding:20px;">Нет данных о занятиях</p>';
            }
        } else { 
            hc.innerHTML = '<p style="color:var(--text-secondary);grid-column:1/-1;text-align:center;padding:20px;">Нет данных о занятиях</p>'; 
        }
    }

    // 4. Instructor Load
    const lc = document.getElementById('instructor-load-chart');
    if (lc) {
        if (load && load.length) {
            const maxB = Math.max(...load.map(l => l.bookings), 1);
            lc.innerHTML = load.map(l => {
                const pct = Math.round((l.bookings / maxB) * 100);
                return `<div class="load-bar-container">
                    <div class="load-bar-label">
                        <span style="font-weight:600;color:var(--text-main);">${escapeHtml(l.name)}</span>
                        <span style="color:var(--text-muted);font-size:12px;"><strong>${l.bookings}</strong> занятий (${pct}%)</span>
                    </div>
                    <div class="load-bar">
                        <div class="load-bar-fill" style="width:${pct}%">${l.bookings > 0 ? l.bookings : ''}</div>
                    </div>
                </div>`;
            }).join('');
        } else { 
            lc.innerHTML = '<p style="color:var(--text-secondary);text-align:center;padding:20px;">Нет данных за последние 30 дней</p>'; 
        }
    }
}

// --- Packages ---
async function loadPackages() {
    const container = document.getElementById('page-packages');
    if (!container) return;

    let packagesHtml = '';
    try {
        const pkgs = await apiGet('/packages');
        if (pkgs && pkgs.length) {
            packagesHtml = `<div class="card" style="margin-bottom:20px">
                <div class="card-header"><div class="card-title">📦 Пакеты занятий</div></div>
                <div id="packages-list">${pkgs.map(p => `<div class="inst-card package-card">
                    <div class="inst-info">
                        <h3>${escapeHtml(p.name)}</h3>
                        <p>${p.sessions_count} занятий · ${p.price.toLocaleString()} ₸ · ${p.validity_days || 30} дней · ${p.bonus_exam ? 'пробный экзамен в подарок' : 'без бонусного экзамена'} · ${p.is_active ? 'Активен' : 'Неактивен'}${p.description ? ' · ' + escapeHtml(p.description) : ''}</p>
                        <p class="assignment-line">${p.assigned_client_name ? `👤 Закреплён: <strong>${escapeHtml(p.assigned_client_name)}</strong>${p.assigned_client_phone ? ` · ${escapeHtml(p.assigned_client_phone)}` : ''}` : '◌ Пока не закреплён за клиентом'}</p>
                    </div>
                    <div style="display:flex;gap:4px">
                        <button class="btn btn-outline btn-sm" onclick="deletePackage(${p.id})">🗑️</button>
                    </div>
                </div>`).join('')}</div>
            </div>`;
        }
    } catch(e) {}

    const certData = await apiGet('/certificates');
    let certHtml = '';
    if (certData && certData.length) {
        certHtml = certData.map(c => `<div class="inst-card package-card">
            <div class="inst-info">
                <h3>${escapeHtml(c.code)}</h3>
                <p>${c.nominal.toLocaleString()} ₸ · Остаток: ${c.remaining.toLocaleString()} ₸ · ${c.is_used ? 'Использован' : 'Активен'}</p>
                <p class="assignment-line">${c.client_name ? `👤 Закреплён: <strong>${escapeHtml(c.client_name)}</strong>${c.client_phone ? ` · ${escapeHtml(c.client_phone)}` : ''}` : '◌ Пока не закреплён за клиентом'}</p>
            </div>
            <button class="btn btn-danger btn-sm" onclick="deleteCertificate(${c.id})">Удалить</button>
        </div>`).join('');
    } else {
        certHtml = '<p style="color:var(--text-secondary)">Нет сертификатов</p>';
    }

    const existingGrid = container.querySelector('.grid-2');
    const existingCertList = container.querySelector('#certificates-list');
    if (existingGrid) {
        const pkgContainer = existingGrid.previousElementSibling;
        if (pkgContainer && pkgContainer.id === 'packages-section') {
            pkgContainer.innerHTML = packagesHtml;
        } else {
            const div = document.createElement('div');
            div.id = 'packages-section';
            div.innerHTML = packagesHtml;
            container.insertBefore(div, existingGrid);
        }
    }
    if (existingCertList) existingCertList.innerHTML = certHtml;
    await loadCertificateRequests(container);
}

async function loadCertificateRequests(container) {
    const data = await apiGet('/certificate-requests');
    if (!data) return;
    let block = container.querySelector('#certificate-requests-section');
    if (!block) {
        block = document.createElement('div');
        block.id = 'certificate-requests-section';
        container.insertBefore(block, container.firstChild);
    }
    const items = data.items || [];
    block.innerHTML = `<div class="card" style="margin-bottom:20px"><div class="card-header"><div class="card-title">⏳ Заявки на активацию сертификатов</div></div>${items.length ? items.map(r => `<div class="inst-card"><div class="inst-info"><h3>${escapeHtml(r.client_name)} · ${escapeHtml(r.code_entered)}</h3><p>${r.matched ? `✅ Код соответствует (${r.certificate_nominal?.toLocaleString()} ₸)` : '❌ Код не соответствует сертификатам'}</p></div><div style="display:flex;gap:6px"><button class="btn btn-success btn-sm" ${r.matched ? '' : 'disabled'} onclick="resolveCertificateRequest(${r.id},'confirm')">Подтвердить</button><button class="btn btn-outline-danger btn-sm" onclick="resolveCertificateRequest(${r.id},'reject')">Отклонить</button></div></div>`).join('') : '<p style="color:var(--text-secondary);padding:12px">Нет заявок на подтверждение</p>'}</div>`;
}

async function resolveCertificateRequest(id, action) {
    try {
        await apiPost(`/certificate-requests/${id}/confirm`, { action });
        showToast(action === 'confirm' ? 'Сертификат подтверждён' : 'Заявка отклонена');
        loadPackages();
    } catch (e) { showToast(e.message, 'error'); }
}

async function deletePackage(id) {
    if (!confirm('Удалить пакет?')) return;
    try {
        await apiDelete(`/packages/${id}`);
        showToast('Пакет удалён');
        loadPackages();
    } catch(e) { showToast(e.message, 'error'); }
}

async function loadCertificates() {
    const data = await apiGet('/certificates');
    if (!data) return;
    const list = document.getElementById('certificates-list');
    if (!data.length) { list.innerHTML = '<p style="color:var(--text-secondary)">Нет сертификатов</p>'; return; }
    list.innerHTML = data.map(c => `<div class="inst-card">
        <div class="inst-info">
            <h3>${escapeHtml(c.code)}</h3>
            <p>${c.nominal.toLocaleString()} ₸ · Остаток: ${c.remaining.toLocaleString()} ₸ · ${c.is_used ? 'Использован' : 'Активен'}${c.client_name ? ' · Клиент: ' + escapeHtml(c.client_name) : ''}</p>
        </div>
        <button class="btn btn-danger btn-sm" onclick="deleteCertificate(${c.id})">Удалить</button>
    </div>`).join('');
}

async function createCertificate() {
    const nominal = parseInt(document.getElementById('cert-nominal').value);
    if (![5000, 10000].includes(nominal)) { showToast('Сертификат может быть только на 5000 или 10000 ₸', 'error'); return; }
    try {
        const data = await apiPost('/certificates', {nominal});
        document.getElementById('cert-result').innerHTML = `<div class="alert alert-success">Код: <strong>${escapeHtml(data.code)}</strong> · Номинал: ${data.nominal} ₸</div>`;
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
        <div style="flex:1;min-width:0">
            <h4>${escapeHtml(f.question)}</h4>
            <p>${escapeHtml(f.answer)}</p>
        </div>
        <div class="faq-actions">
            <button class="btn btn-outline btn-sm" onclick="editFaq(${f.id})">✏️</button>
            <button class="btn btn-danger btn-sm" onclick="deleteFaq(${f.id})">🗑️</button>
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

async function editFaq(id) {
    const data = await apiGet('/faq');
    if (!data) return;
    const item = data.find(f => f.id === id);
    if (!item) { showToast('Вопрос не найден', 'error'); return; }

    document.getElementById('modal-title').textContent = 'Редактировать вопрос';
    document.getElementById('modal-body').innerHTML = `
        <div class="form-group">
            <label>Вопрос</label>
            <input type="text" id="edit-faq-question" value="${escapeHtml(item.question)}">
        </div>
        <div class="form-group">
            <label>Ответ</label>
            <textarea id="edit-faq-answer" rows="4">${escapeHtml(item.answer)}</textarea>
        </div>
    `;
    document.getElementById('modal-save-btn').style.display = 'block';
    document.getElementById('modal-save-btn').onclick = async () => {
        const question = document.getElementById('edit-faq-question').value.trim();
        const answer = document.getElementById('edit-faq-answer').value.trim();
        if (!question || !answer) { showToast('Заполните вопрос и ответ', 'error'); return; }
        try {
            await apiPut(`/faq/${id}`, { question, answer });
            showToast('Вопрос обновлён');
            closeModal();
            loadFaq();
        } catch (e) {
            showToast(e.message, 'error');
        }
    };
    document.getElementById('app-modal').classList.remove('hidden');
}

async function deleteFaq(id) {
    if (!confirm('Удалить этот вопрос?')) return;
    await apiDelete(`/faq/${id}`);
    showToast('Вопрос удалён');
    loadFaq();
}

// --- Archive ---
async function loadArchive() {
    const button = document.getElementById('archive-completed-btn');
    const container = document.getElementById('archive-list');
    if (!button || !container) return;

    button.disabled = true;
    container.innerHTML = '<p style="color:var(--text-secondary);padding:16px 0;">Загрузка архива...</p>';
    try {
        const bookings = await apiGet('/bookings/archive');
        if (!Array.isArray(bookings)) throw new Error('Сервер вернул некорректные данные архива');
        if (!bookings.length) {
            container.innerHTML = '<p style="color:var(--text-secondary);padding:16px 0;">В архиве пока нет завершённых записей.</p>';
            return;
        }
        container.innerHTML = `
            <p style="color:var(--text-secondary);margin:0 0 12px;">Загружено записей: ${bookings.length}</p>
            <div class="table-container table-wrapper">
              <table class="data-table">
                <thead><tr><th>Дата</th><th>Время</th><th>Клиент</th><th>Инструктор</th><th>Услуга</th><th>КПП</th><th>Площадка</th><th>Оплата</th></tr></thead>
                <tbody>${bookings.map(booking => `<tr>
                    <td>${escapeHtml(booking.date || '—')}</td>
                    <td>${escapeHtml(String(booking.start_time || '—').slice(0, 5))}</td>
                    <td><strong>${escapeHtml(booking.client_name || '—')}</strong><br><small style="color:var(--text-secondary)">${escapeHtml(booking.client_phone || '')}</small></td>
                    <td>${escapeHtml(booking.instructor_name || '—')}</td>
                    <td>${booking.service_type === 'training' ? 'Обучение' : booking.service_type === 'exam' ? 'Экзамен' : escapeHtml(booking.service_type || '—')}</td>
                    <td>${booking.transmission === 'manual' ? 'МКПП' : booking.transmission === 'automatic' ? 'АКПП' : escapeHtml(booking.transmission || '—')}</td>
                    <td>${escapeHtml(booking.location || '—')}</td>
                    <td>${paymentSummary(booking)}</td>
                </tr>`).join('')}</tbody>
              </table>
            </div>`;
    } catch (error) {
        container.innerHTML = `<p style="color:var(--danger);padding:16px 0;">${escapeHtml(error.message || 'Не удалось выгрузить архив')}</p>`;
    } finally {
        button.disabled = false;
    }
}

async function loadArchivedLogSection(kind) {
    const button = document.getElementById(`archive-${kind}-btn`);
    const container = document.getElementById('archive-list');
    if (!button || !container) return;

    button.disabled = true;
    container.innerHTML = '<p style="color:var(--text-secondary);padding:16px 0;">Загрузка архива...</p>';
    try {
        const rows = await apiGet(`/logs/archive/${kind === 'events' ? 'events' : 'audit'}`);
        if (!Array.isArray(rows)) throw new Error('Сервер вернул некорректные данные архива');
        const isAudit = kind === 'audit';
        const title = isAudit ? 'Архив аудита' : 'Архив событий';
        if (!rows.length) {
            container.innerHTML = `<p style="color:var(--text-secondary);padding:16px 0;">В ${isAudit ? 'архиве аудита' : 'архиве событий'} пока нет записей.</p>`;
            return;
        }
        container.innerHTML = `
            <p style="color:var(--text-secondary);margin:0 0 12px;">${title}: ${rows.length} записей</p>
            <div class="table-container table-wrapper">
              <table class="data-table">
                <thead><tr><th>Дата и время</th><th>${isAudit ? 'Администратор' : 'Источник'}</th><th>${isAudit ? 'Действие' : 'Событие'}</th><th>Описание</th><th>Клиент</th><th>Запись</th></tr></thead>
                <tbody>${rows.map(row => `<tr>
                    <td>${escapeHtml(String(row.created_at || '—').replace('T', ' ').slice(0, 19))}</td>
                    <td>${escapeHtml(isAudit ? (row.admin_username || '—') : (row.event_source || '—'))}</td>
                    <td>${escapeHtml(isAudit ? (row.action || '—') : (row.event_type || '—'))}</td>
                    <td>${escapeHtml(isAudit ? (row.details || '—') : (row.message || '—'))}</td>
                    <td>${escapeHtml(String(row.client_id || '—'))}</td>
                    <td>${escapeHtml(String(row.booking_id || '—'))}</td>
                </tr>`).join('')}</tbody>
              </table>
            </div>`;
    } catch (error) {
        container.innerHTML = `<p style="color:var(--danger);padding:16px 0;">${escapeHtml(error.message || 'Не удалось загрузить архив')}</p>`;
    } finally {
        button.disabled = false;
    }
}

async function loadArchivedAudit() {
    return loadArchivedLogSection('audit');
}

async function loadArchivedEvents() {
    return loadArchivedLogSection('events');
}

// --- Notifications ---
async function loadNotifications() {
    const data = await apiGet('/notifications');
    if (!data) return;
    const list = document.getElementById('notifications-list');
    if (!data.length) { list.innerHTML = '<p style="color:var(--text-secondary)">Пока нет действий клиентов</p>'; return; }
    const icons = {
        low_rating: '⚠️', new_booking: '📋', edit_booking: '✏️',
        reassign_booking: '🔄', booking_rescheduled: '🔄', booking_cancelled: '❌',
        booking_confirmed: '✅', new_client: '👤', no_show: '🚫',
        rating_given: '⭐', app_rating_given: '⭐', lesson_completed: '🏁',
        booking_cancellation_requested: '❌', booking_cancellation_revoked: '↩️',
        booking_reschedule_requested: '🔄', booking_attendance_confirmed: '✅',
        client_registered: '👤', client_profile_linked: '🔗', client_profile_reactivated: '👤',
        client_profile_updated: '✏️', client_avatar_updated: '🖼️', client_avatar_removed: '🖼️',
        client_support_message: '💬', client_support_closed: '💬',
        package_requested: '📦', certificate_activation_requested: '🎟️',
    };
    list.innerHTML = data.map(n => `<div class="notif-item notif-${n.type}">
        <div class="notif-icon">${icons[n.type]||'🔔'}</div>
        <div class="notif-body">
            <h4>${escapeHtml(n.message)}</h4>
            <p>${new Date(n.created_at).toLocaleString('ru-RU')}</p>
        </div>
    </div>`).join('');
}

// --- Audit ---
async function loadAudit() {
    const data = await apiGet('/audit-logs');
    if (!data) return;
    
    const tbody = document.querySelector('#audit-table tbody');
    const mobileCards = document.getElementById('audit-mobile-cards');
    
    if (!data.length) { 
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-secondary);padding:32px">Нет записей</td></tr>'; 
        if (mobileCards) mobileCards.innerHTML = '<p style="color:var(--text-secondary);text-align:center;padding:32px">Нет событий</p>';
        return; 
    }
    
    // Desktop таблица
    tbody.innerHTML = data.map(l => `<tr>
        <td>${new Date(l.created_at).toLocaleString('ru-RU')}</td>
        <td>${escapeHtml(l.action)}</td>
        <td><strong>${escapeHtml(l.admin_username)}</strong></td>
        <td style="color:var(--text-secondary)">${escapeHtml(l.details||'—')}</td>
    </tr>`).join('');
    
    // Mobile карточки
    if (mobileCards) {
        mobileCards.innerHTML = data.map(l => `
            <div class="mobile-card">
                <div class="mobile-card-header">
                    <div class="mobile-card-title">${escapeHtml(l.action)}</div>
                    <span style="font-size:11px;color:var(--text-secondary)">${new Date(l.created_at).toLocaleString('ru-RU', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}</span>
                </div>
                <div class="mobile-card-row">
                    <span class="mobile-card-label">Администратор:</span>
                    <span class="mobile-card-value"><strong>${escapeHtml(l.admin_username)}</strong></span>
                </div>
                ${l.details ? `
                <div class="mobile-card-row">
                    <span class="mobile-card-label">Описание:</span>
                    <span class="mobile-card-value" style="font-size:12px">${escapeHtml(l.details)}</span>
                </div>
                ` : ''}
            </div>
        `).join('');
    }
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


// --- Support ---
let currentDialogUserId = null;
let currentSupportChannel = 'clients';

function switchSupportChannel(channel) {
    currentSupportChannel = channel;
    currentDialogUserId = null;
    document.getElementById('support-tab-clients')?.classList.toggle('active', channel === 'clients');
    document.getElementById('support-tab-instructors')?.classList.toggle('active', channel === 'instructors');
    const title = document.getElementById('support-list-title');
    if (title) title.textContent = channel === 'clients' ? 'Диалоги с клиентами' : 'Инструкторы';
    document.getElementById('chat-empty').classList.remove('hidden');
    document.getElementById('chat-active').classList.add('hidden');
    document.getElementById('chat-close-button')?.classList.add('hidden');
    loadSupport();
}

async function loadSupport() {
    const dialogs = await apiGet(currentSupportChannel === 'clients' ? '/support/dialogs' : '/support/instructors/dialogs');
    if (!dialogs) return;
    const list = document.getElementById('support-dialogs');
    if (!dialogs.length) {
        list.innerHTML = '<p style="color:var(--text-secondary);padding:32px 16px;text-align:center">Нет диалогов</p>';
        return;
    }
    list.innerHTML = dialogs.map(d => {
        const initial = escapeHtml(d.user_name ? d.user_name.charAt(0).toUpperCase() : '?');
        const isActive = currentDialogUserId === d.user_id;
        const channelBadge = d.channel === 'telegram' ? '<span style="font-size:10px;background:#0088cc;color:#fff;padding:1px 5px;border-radius:3px;margin-left:4px">TG</span>' : '';
        return `
        <div class="support-dialog-item ${d.has_new ? 'has-new' : ''} ${isActive ? 'active' : ''}" onclick="openDialog(${d.user_id})">
            <div class="dialog-avatar">${initial}</div>
            <div class="dialog-info">
                <div class="dialog-header">
                    <strong class="dialog-name">${escapeHtml(d.user_name)}${channelBadge}</strong>
                    <span class="dialog-time">${d.last_message_at ? new Date(d.last_message_at).toLocaleString('ru-RU', {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}) : ''}</span>
                </div>
                <div class="dialog-preview">${escapeHtml(d.last_message || 'Нет сообщений')}</div>
            </div>
            ${d.has_new ? `<div class="dialog-unread-badge">${d.unread_from_user}</div>` : ''}
            <button class="dialog-delete-btn" onclick="event.stopPropagation();deleteDialog(${d.user_id})" title="Удалить диалог">🗑️</button>
        </div>`;
    }).join('');
}

async function openDialog(userId) {
    currentDialogUserId = userId;
    const data = await apiGet(currentSupportChannel === 'clients' ? `/support/dialogs/${userId}` : `/support/instructors/dialogs/${userId}`);
    if (!data) return;

    document.getElementById('chat-empty').classList.add('hidden');
    document.getElementById('chat-active').classList.remove('hidden');
    document.getElementById('chat-user-name').textContent = data.user.name;
    document.getElementById('chat-user-contacts').textContent = data.user.phone || '—';
    document.getElementById('chat-close-button')?.classList.toggle(
        'hidden', currentSupportChannel !== 'clients' || !data.user.support_chat_is_open,
    );

    const chatMessages = document.getElementById('chat-messages');
    chatMessages.innerHTML = data.messages.map(m => `
        <div class="chat-message ${m.sender === 'admin' ? 'chat-message-admin' : 'chat-message-user'}">
            <div class="chat-message-text">${escapeHtml(m.text)}</div>
            <div class="chat-message-time">${new Date(m.created_at).toLocaleString('ru')}</div>
        </div>
    `).join('');
    chatMessages.scrollTop = chatMessages.scrollHeight;

    // Сервер отметил прочитанными только сообщения этого диалога. Сразу
    // пересчитываем общий бейдж и список, не затрагивая остальные чаты.
    await pollNotificationCounts();
    await loadSupport();
}

async function sendReply() {
    if (!currentDialogUserId) return;
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;

    const result = await apiPost(currentSupportChannel === 'clients' ? `/support/dialogs/${currentDialogUserId}/reply` : `/support/instructors/dialogs/${currentDialogUserId}/reply`, { text });
    if (!result) return;

    input.value = '';
    openDialog(currentDialogUserId);  // Refresh chat
}

async function closeSupportChat() {
    if (currentSupportChannel !== 'clients' || !currentDialogUserId) return;
    if (!confirm('Завершить чат с клиентом?')) return;
    const result = await apiPost(`/support/dialogs/${currentDialogUserId}/close`, {});
    if (!result) return;
    showToast('Чат завершён');
    await openDialog(currentDialogUserId);
}

async function deleteDialog(userId) {
    const userName = document.getElementById('chat-user-name')?.textContent || 'этим пользователем';
    if (!confirm(`Удалить диалог с "${userName}"?\n\nВсе сообщения будут удалены.`)) return;
    try {
        await apiDelete(currentSupportChannel === 'clients' ? `/support/dialogs/${userId}` : `/support/instructors/dialogs/${userId}`);
        showToast('Диалог удалён');
        if (currentDialogUserId === userId) {
            currentDialogUserId = null;
            document.getElementById('chat-empty').classList.remove('hidden');
            document.getElementById('chat-active').classList.add('hidden');
        }
        loadSupport();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// Enter для отправки
document.getElementById('chat-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendReply();
    }
});

// --- Clients ---
async function toggleClientHistory(clientId) {
    try {
        // Получаем данные клиента
        const clients = await apiGet('/clients');
        const client = clients.find(c => c.id === clientId);
        if (!client) {
            showToast('Клиент не найден', 'error');
            return;
        }

        // Получаем историю клиента
        const history = await apiGet(`/clients/${clientId}/history`);
        
        if (!history || history.length === 0) {
            document.getElementById('modal-title').textContent = `📜 История: ${client.name}`;
            document.getElementById('modal-body').innerHTML = `
                <p style="color:var(--text-secondary);text-align:center;padding:40px 20px">
                    История пуста — нет событий
                </p>
            `;
            document.getElementById('modal-save-btn').style.display = 'none';
            document.getElementById('app-modal').classList.remove('hidden');
            return;
        }

        // Группируем события по типам для лучшей визуализации
        const eventTypeLabels = {
            booking: 'Записи',
            certificate: 'Сертификаты',
            audit: 'События'
        };

        // Формируем HTML с событиями
        const eventsHtml = history.map(ev => {
            let iconColor = 'var(--primary)';
            if (ev.status === 'completed') iconColor = 'var(--success)';
            if (ev.status === 'cancelled' || ev.status === 'no_show') iconColor = 'var(--danger)';
            
            return `
                <div class="client-history-event" style="padding:14px;margin-bottom:10px;border-left:3px solid ${iconColor};background:white;border-radius:0 8px 8px 0;box-shadow:0 1px 3px rgba(0,0,0,0.04)">
                    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:6px">
                        <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:0">
                            <span style="font-size:20px;flex-shrink:0">${escapeHtml(ev.icon)}</span>
                            <strong style="font-size:14px;word-break:break-word">${escapeHtml(ev.title)}</strong>
                        </div>
                        <span style="color:var(--text-secondary);font-size:11px;white-space:nowrap;flex-shrink:0">
                            ${new Date(ev.date).toLocaleString('ru-RU', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'})}
                        </span>
                    </div>
                    <p style="margin:0 0 0 32px;font-size:13px;color:var(--text-secondary);word-break:break-word">
                        ${escapeHtml(ev.description)}
                    </p>
                </div>
            `;
        }).join('');

        // Статистика
        const stats = {
            total: history.length,
            bookings: history.filter(e => e.type === 'booking').length,
            certificates: history.filter(e => e.type === 'certificate').length,
            completed: history.filter(e => e.status === 'completed').length,
        };

        document.getElementById('modal-title').textContent = `📜 История: ${client.name}`;
        document.getElementById('modal-body').innerHTML = `
            <div style="margin-bottom:16px;padding:12px;background:var(--bg);border-radius:8px;display:flex;gap:16px;flex-wrap:wrap;font-size:13px">
                <div><strong>Всего событий:</strong> ${stats.total}</div>
                <div><strong>Записей:</strong> ${stats.bookings}</div>
                ${stats.certificates > 0 ? `<div><strong>Сертификатов:</strong> ${stats.certificates}</div>` : ''}
                ${stats.completed > 0 ? `<div><strong>Завершено:</strong> ${stats.completed}</div>` : ''}
            </div>
            <div style="max-height:60vh;overflow-y:auto;-webkit-overflow-scrolling:touch">
                ${eventsHtml}
            </div>
        `;
        document.getElementById('modal-save-btn').style.display = 'none';
        document.getElementById('app-modal').classList.remove('hidden');

    } catch (e) {
        showToast(e.message || 'Ошибка загрузки истории', 'error');
    }
}

let _clientsData = [];
let _selectedClientId = null;

async function loadClients() {
    const data = await apiGet('/clients');
    if (!data) return;
    _clientsData = data;
    _renderClientsList(data);
}

function _renderClientsList(data) {
    const list = document.getElementById('clients-list');
    if (!data.length) {
        list.innerHTML = '<p style="color:var(--text-secondary);padding:16px">Нет клиентов</p>';
        return;
    }
    list.innerHTML = data.map(c => `
        <div class="client-list-item ${_selectedClientId === c.id ? 'active' : ''}" onclick="selectClient(${c.id})">
            <div class="client-list-avatar">${escapeHtml(c.name.charAt(0).toUpperCase())}</div>
            <div class="client-list-info">
                <div class="client-list-name">${escapeHtml(c.name)}</div>
                <div class="client-list-phone">${escapeHtml(c.phone || '—')}</div>
            </div>
            ${c.bookings_count > 0 ? `<span class="client-list-bookings">${c.bookings_count}</span>` : ''}
        </div>
    `).join('');
}

function filterClients() {
    const q = document.getElementById('client-search').value.toLowerCase();
    const filtered = _clientsData.filter(c => c.name.toLowerCase().includes(q) || (c.phone && c.phone.includes(q)));
    _renderClientsList(filtered);
}

async function selectClient(clientId) {
    _selectedClientId = clientId;
    const client = _clientsData.find(c => c.id === clientId);
    if (!client) return;

    document.getElementById('client-empty').classList.add('hidden');
    const card = document.getElementById('client-card');
    card.classList.remove('hidden');

    card.innerHTML = `
        <div class="client-mini-header">
            <div class="client-mini-avatar">${escapeHtml(client.name.charAt(0).toUpperCase())}</div>
            <div>
                <h3>${escapeHtml(client.name)}</h3>
                <p style="color:var(--text-secondary);font-size:13px;margin:0">${escapeHtml(client.phone || '—')}</p>
            </div>
        </div>
        <div class="client-mini-details">
            ${client.telegram_id ? `<div class="client-mini-row"><span class="client-mini-label">Telegram:</span><span>${escapeHtml(client.telegram_id)}</span></div>` : ''}
            <div class="client-mini-row"><span class="client-mini-label">Реферал:</span><span><strong>${escapeHtml(client.referral_code)}</strong> ${client.referral_discount_available ? '🎁' : ''}</span></div>
            <div class="client-mini-row"><span class="client-mini-label">Записей:</span><span><strong>${client.bookings_count}</strong></span></div>
            ${client.certificates && client.certificates.length > 0 ? `
                <div class="client-mini-row"><span class="client-mini-label">Сертификаты:</span><span>${client.certificates.map(cert =>
                    `<span class="badge ${cert.is_used ? 'badge-secondary' : 'badge-success'}" style="margin:2px">${escapeHtml(cert.code)} (${cert.nominal}₸)</span>`
                ).join('')}</span></div>
            ` : ''}
            ${client.packages && client.packages.length > 0 ? `
                <div class="client-mini-row"><span class="client-mini-label">Пакеты:</span><span>${client.packages.map(pkg =>
                    `<span class="badge badge-info" style="margin:2px">${escapeHtml(pkg.name)} (${pkg.remaining_sessions}/${pkg.sessions_count})</span>`
                ).join('')}</span></div>
            ` : ''}
            <div class="client-mini-row" style="color:var(--text-secondary);font-size:12px"><span class="client-mini-label">Создан:</span><span>${new Date(client.created_at).toLocaleString('ru-RU')}</span></div>
        </div>
        <div class="client-mini-actions">
            <button class="btn btn-outline btn-sm" onclick="toggleClientHistory(${client.id})">📜 История</button>
            <button class="btn btn-outline btn-sm" onclick="editClient(${client.id})">✏️ Редактировать</button>
            <button class="btn btn-danger btn-sm" onclick="deleteClient(${client.id})">🗑️ Удалить</button>
        </div>
    `;

    // Re-render list to show active state
    _renderClientsList(_clientsData.filter(c => {
        const q = document.getElementById('client-search').value.toLowerCase();
        return c.name.toLowerCase().includes(q) || (c.phone && c.phone.includes(q));
    }));
}

function showClientForm() {
    document.getElementById('client-name').value = '';
    document.getElementById('client-phone').value = '';
    document.getElementById('client-referral').value = '';
    document.getElementById('client-password').value = '';
    const form = document.getElementById('client-form');
    form.classList.remove('hidden');
    form.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function hideClientForm() {
    document.getElementById('client-form').classList.add('hidden');
}

async function saveClient() {
    const name = document.getElementById('client-name').value.trim();
    const phone = document.getElementById('client-phone').value.trim();
    const referral = document.getElementById('client-referral').value.trim();
    const password = document.getElementById('client-password').value.trim();
    
    if (!password) {
        showToast('Введите пароль для клиента', 'error');
        return;
    }
    if (!isValidClientPassword(password)) {
        showToast('Пароль: минимум 6 символов, латинские буквы, цифры и символы без пробелов', 'error');
        return;
    }
    
    try {
        await apiPost('/clients', {
            name,
            phone: phone || null,
            referral_code: referral || null,
            password,
        });
        showToast('Клиент добавлен');
        hideClientForm();
        loadClients();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function editClient(id) {
    try {
        const clients = await apiGet('/clients');
        const client = clients.find(c => c.id === id);
        if (!client) {
            showToast('Клиент не найден', 'error');
            return;
        }

        document.getElementById('modal-title').textContent = 'Редактировать клиента';
        document.getElementById('modal-body').innerHTML = `
            <div class="form-group">
                <label>Имя</label>
                <input type="text" id="edit-client-name" value="${escapeHtml(client.name)}">
            </div>
            <div class="form-group">
                <label>Телефон</label>
                <input type="text" id="edit-client-phone" value="${escapeHtml(client.phone || '')}">
            </div>
            <div class="form-group">
                <label>Назначить новый пароль</label>
                <input type="password" id="edit-client-password" autocomplete="new-password" placeholder="Оставьте пустым, чтобы не менять">
                <small style="color:var(--text-secondary);font-size:12px;margin-top:4px;display:block">Текущий пароль не отображается. Новый пароль перезапишет его после сохранения.</small>
            </div>
            <div class="form-group">
                <label>Реферальный код</label>
                <input type="text" id="edit-client-referral" value="${escapeHtml(client.referral_code || '')}">
            </div>
        `;
        document.getElementById('modal-save-btn').style.display = 'block';
        document.getElementById('modal-save-btn').onclick = async () => {
            const name = document.getElementById('edit-client-name').value.trim();
            const phone = document.getElementById('edit-client-phone').value.trim();
            const referral = document.getElementById('edit-client-referral').value.trim();
            const password = document.getElementById('edit-client-password').value;

            if (!name) {
                showToast('Введите имя', 'error');
                return;
            }
            if (password && !isValidClientPassword(password)) {
                showToast('Пароль: минимум 6 символов, латинские буквы, цифры и символы без пробелов', 'error');
                return;
            }

            try {
                await apiPut(`/clients/${id}`, {
                    name,
                    phone,
                    referral_code: referral,
                    ...(password ? { password } : {}),
                });
                showToast('Клиент обновлён');
                closeModal();
                loadClients();
            } catch (e) {
                showToast(e.message, 'error');
            }
        };
        document.getElementById('app-modal').classList.remove('hidden');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deleteClient(id) {
    const name = _clientsData.find(client => client.id === id)?.name || 'этого клиента';
    if (!confirm(`Удалить клиента "${name}"?\n\nБудут безвозвратно удалены его записи, переписка поддержки, сертификаты, пакеты и доступ к APK. События, ограничения и реферальные связи сохранятся. Это действие нельзя отменить.`)) {
        return;
    }
    
    try {
        await apiDelete(`/clients/${id}`);
        showToast('Клиент удалён');
        loadClients();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function viewClientBookings(clientId) {
    try {
        const bookings = await apiGet(`/clients/${clientId}/bookings`);
        if (!bookings) return;
        
        if (bookings.length === 0) {
            showToast('У клиента нет записей', 'error');
            return;
        }
        
        const html = `
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Дата</th>
                            <th>Время</th>
                            <th>Услуга</th>
                            <th>Инструктор</th>
                            <th>Статус</th>
                            <th>Цена</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${bookings.map(b => `
                            <tr>
                                <td>${b.booking_date}</td>
                                <td>${b.start_time?.slice(0,5)}</td>
                                <td>${b.service_type === 'training' ? 'Урок' : 'Экзамен'}</td>
                                <td>${b.instructor_name || '—'}</td>
                                <td>${statusBadge(b.status)}</td>
                                <td><strong>${b.price}₸</strong></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
        
        document.getElementById('modal-title').textContent = 'История записей клиента';
        document.getElementById('modal-body').innerHTML = html;
        document.getElementById('modal-save-btn').style.display = 'none';
        document.getElementById('app-modal').classList.remove('hidden');
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function closeModal() {
    document.getElementById('app-modal').classList.add('hidden');
    document.getElementById('modal-save-btn').style.display = 'block';
}

// --- Page init ---
document.addEventListener('DOMContentLoaded', async () => {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/sw.js').catch(() => {
            // The application still works online if a host blocks workers.
        });
    }
    // Проверяем активную сессию
    try {
        const res = await fetch(`${API}/check-session`, {
            credentials: 'include',
            cache: 'no-store',
        });
        const data = res.ok ? await res.json().catch(() => null) : null;
        if (res.ok && data?.ok === true) {
            // Сессия активна - показываем дашборд
            showDashboard();
        } else {
            // Сессия не активна - показываем логин
            showLogin();
        }
    } catch { showLogin(); }
});

// --- Manual Bookings ---
// State from the working pre-redesign form. Without these declarations the
// module throws ReferenceError as soon as the manual form is opened.
let manualBookingInstructors = [];
let manualClientSuggestions = [];
let manualClientSearchTimer = null;
let slotsLoadRequestId = 0;

async function showManualBookingForm() {
    document.getElementById('manual-booking-form').classList.remove('hidden');
    // Загружаем инструкторов
    try {
        const instructors = await apiGet('/instructors');
        if (!Array.isArray(instructors)) {
            throw new Error('Сервер вернул некорректный список инструкторов');
        }
        manualBookingInstructors = instructors;
        const select = document.getElementById('mb-instructor');
        select.innerHTML = '<option value="">Автоматически подобрать</option>' + manualBookingInstructors
            // Старые и импортированные записи могли не иметь is_active. В таком
            // случае инструктор считается активным; исключаем только явно выключенных.
            .filter(i => i.is_active !== false)
            .map(i => `
            <option value="${i.id}" data-transmission="${escapeHtml(i.transmission || '')}">${escapeHtml(i.name)}</option>
        `).join('');
        select.onchange = () => {
            updateManualTransmissionFromInstructor();
            loadSlots();
        };
        updateManualTransmissionFromInstructor();
    } catch (e) {
        showToast(e.message || 'Не удалось загрузить список инструкторов', 'error');
        return;
    }
    
    const dateInput = document.getElementById('mb-date');
    try {
        const snapshot = isAdminOffline ? await offlineRead('api-cache', 'offline-snapshot') : null;
        const window = snapshot?.booking_window || await apiGet('/booking-window');
        dateInput.setAttribute('min', window.min_date);
        dateInput.setAttribute('max', window.max_date);
        dateInput.value = window.min_date;
    } catch (e) {
        showToast(e.message || 'Не удалось получить доступный период записи', 'error');
        return;
    }
    
    // Даём браузеру применить установленную дату перед первым запросом.
    // Поэтому слоты выбранного по умолчанию дня показываются сразу при открытии.
    await new Promise(resolve => requestAnimationFrame(resolve));
    await loadSlots();
}

function hideManualBookingForm() {
    document.getElementById('manual-booking-form').classList.add('hidden');
}

function hideManualClientSuggestions() {
    document.getElementById('mb-client-suggestions')?.classList.add('hidden');
}

function selectManualClient(clientId) {
    const client = manualClientSuggestions.find(c => String(c.id) === String(clientId));
    if (!client) return;
    document.getElementById('mb-name').value = client.name || '';
    document.getElementById('mb-phone').value = client.phone || '';
    hideManualClientSuggestions();
}

function searchManualClients(source) {
    clearTimeout(manualClientSearchTimer);
    manualClientSearchTimer = setTimeout(async () => {
        const name = document.getElementById('mb-name').value.trim();
        const phone = document.getElementById('mb-phone').value.trim();
        const query = source === 'phone' ? phone : name;
        const box = document.getElementById('mb-client-suggestions');
        if (!box || query.length < 2) {
            hideManualClientSuggestions();
            return;
        }
        try {
            if (isAdminOffline) {
                const snapshot = await offlineRead('api-cache', 'offline-snapshot');
                const needle = query.toLowerCase();
                manualClientSuggestions = (snapshot?.clients || []).filter(c =>
                    String(c.name || '').toLowerCase().includes(needle) || String(c.phone || '').includes(query)
                ).slice(0, 10);
            } else {
                manualClientSuggestions = await apiGet(`/clients/search?q=${encodeURIComponent(query)}`);
            }
        } catch (_) {
            hideManualClientSuggestions();
            return;
        }
        if (!manualClientSuggestions || !manualClientSuggestions.length) {
            hideManualClientSuggestions();
            return;
        }
        box.innerHTML = manualClientSuggestions.map(c => `
            <button type="button" class="client-suggestion-item" onclick="selectManualClient(${c.id})">
                <strong>${escapeHtml(c.name)}</strong>
                <span>${escapeHtml(c.phone || 'телефон не указан')} · записей: ${c.bookings_count}</span>
            </button>
        `).join('');
        box.classList.remove('hidden');
    }, 250);
}

async function loadSlots() {
    const requestId = ++slotsLoadRequestId;
    const date = document.getElementById('mb-date').value;
    const service = document.getElementById('mb-service').value;
    const transmission = document.getElementById('mb-transmission').value;
    const instructorId = document.getElementById('mb-instructor').value;
    
    if (!date) {
        document.getElementById('slots-panel').innerHTML = '<div class="slots-list"><p style="color:var(--text-secondary);text-align:center;padding:24px 0;font-size:13px">Выберите дату</p></div>';
        return;
    }
    
    // Формируем URL с параметрами
    let url = `/slots?booking_date=${date}&service_type=${service}&transmission=${transmission}`;
    if (instructorId) {
        url += `&instructor_id=${instructorId}`;
    }
    
    let data;
    if (isAdminOffline) {
        data = await buildOfflineSlots(date, service, transmission, instructorId);
    } else {
        try {
            data = await apiGet(url);
        } catch (error) {
            if (!isAdminOffline) throw error;
            data = await buildOfflineSlots(date, service, transmission, instructorId);
        }
    }
    // Если пока выполнялся запрос пользователь уже сменил дату, инструктора
    // или КПП, не даём старому ответу перерисовать актуальные слоты.
    if (requestId !== slotsLoadRequestId) return;

    if (!data || !data.slots || data.slots.length === 0) {
        document.getElementById('slots-panel').innerHTML = '<div class="slots-list"><p style="color:var(--text-secondary);text-align:center;padding:24px 0;font-size:13px">Нет доступных слотов на эту дату</p></div>';
        return;
    }
    
    const panel = document.getElementById('slots-panel');
    
    // Заголовок: показываем для какого инструктора отображаются слоты
    let header = '';
    if (instructorId) {
        const instructorSelect = document.getElementById('mb-instructor');
        const instructorName = instructorSelect.options[instructorSelect.selectedIndex].text;
        header = `<p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;text-align:center">📋 Слоты для: <strong>${instructorName}</strong></p>`;
    }
    
    panel.innerHTML = header + '<div class="slots-list">' + data.slots.map(slot => {
        const hasBookings = slot.bookings && slot.bookings.length > 0;
        const status = slot.is_free ? 'free' : 'busy';
        const statusText = slot.is_free
            ? (hasBookings ? `Есть место · занято ${slot.booked_count || slot.bookings.length}/${slot.capacity || 6}` : `Свободно · 0/${slot.capacity || 6}`)
            : (hasBookings ? `Занято · ${slot.booked_count || slot.bookings.length}/${slot.capacity || 6}` : 'Недоступно');
        
        // Формируем информацию о занятости
        let info = '';
        if (slot.bookings.length > 0) {
            if (instructorId) {
                // Если инструктор выбран, показываем только клиента
                info = slot.bookings.map(b => `Клиент: ${b.client}`).join(', ');
            } else {
                // Если инструктор не выбран, показываем клиента и инструктора
                info = slot.bookings.map(b => `${b.client} → ${b.instructor}`).join(', ');
            }
        }
        
        return `
            <div class="slot-item slot-${status}" ${slot.is_free ? `onclick="selectSlot('${slot.time}', ${isAdminOffline ? (slot.recommended_instructor_id || 'null') : 'null'})"` : ''}>
                <div class="slot-header">
                    <span class="slot-time">${slot.time} — ${slot.end_time}</span>
                    <span class="slot-dot"></span>
                </div>
                <span class="slot-status">${statusText}</span>
                ${info ? `<span class="slot-info">${info}</span>` : ''}
                ${!instructorId && slot.available_instructors_count ? `<span class="slot-info">Подходит инструкторов: ${slot.available_instructors_count}</span>` : ''}
            </div>
        `;
    }).join('') + '</div>';
}

function handleServiceChange() {
    const service = document.getElementById('mb-service').value;
    const locationSelect = document.getElementById('mb-location');
    const transmissionSelect = document.getElementById('mb-transmission');
    
    if (service === 'exam') {
        locationSelect.innerHTML = '<option value="Циолковского 30">Циолковского 30 (5000₸)</option>';
        locationSelect.disabled = true;
        transmissionSelect.value = 'automatic';
        transmissionSelect.disabled = true;
    } else {
        locationSelect.innerHTML = '<option value="Циолковского 30">Циолковского 30 (10000₸)</option>';
        locationSelect.disabled = true;
        transmissionSelect.disabled = false;
    }
    updateManualTransmissionFromInstructor();
    
    loadSlots();
}

function updateManualTransmissionFromInstructor() {
    const instructorSelect = document.getElementById('mb-instructor');
    const transmissionSelect = document.getElementById('mb-transmission');
    if (!instructorSelect || !transmissionSelect) return;
    if (document.getElementById('mb-service').value === 'exam') {
        transmissionSelect.value = 'automatic';
        return;
    }
    const instructorId = instructorSelect.value;
    if (!instructorId || !manualBookingInstructors.length) {
        transmissionSelect.disabled = false;
        return;
    }
    const instructor = manualBookingInstructors.find(i => String(i.id) === String(instructorId));
    const selectedOption = instructorSelect.options[instructorSelect.selectedIndex];
    const instructorTransmission = String(
        selectedOption?.dataset?.transmission || instructor?.transmission || ''
    ).toLowerCase();
    if (instructorTransmission.includes('automatic') || instructorTransmission.includes('автомат') || instructorTransmission.includes('акпп') || instructorTransmission === 'auto') {
        transmissionSelect.value = 'automatic';
    } else if (instructorTransmission.includes('manual') || instructorTransmission.includes('механ') || instructorTransmission.includes('мкпп')) {
        transmissionSelect.value = 'manual';
    } else {
        transmissionSelect.disabled = false;
    }
}

function selectSlot(time, recommendedInstructorId = null) {
    document.querySelectorAll('.slot-item').forEach(el => el.classList.remove('slot-selected'));
    const slotEl = document.querySelector(`.slot-item[onclick*="'${time}'"]`);
    if (slotEl) slotEl.classList.add('slot-selected');
    document.getElementById('mb-time').value = time;
    if (recommendedInstructorId) {
        const instructorSelect = document.getElementById('mb-instructor');
        if (instructorSelect && !instructorSelect.value) {
            instructorSelect.value = String(recommendedInstructorId);
            updateManualTransmissionFromInstructor();
        }
    }
}

async function saveManualBooking() {
    const name = document.getElementById('mb-name').value.trim();
    const phone = document.getElementById('mb-phone').value.trim();
    const instructorId = parseInt(document.getElementById('mb-instructor').value);
    const service = document.getElementById('mb-service').value;
    const location = document.getElementById('mb-location').value;
    const transmission = document.getElementById('mb-transmission').value;
    const date = document.getElementById('mb-date').value;
    const time = document.getElementById('mb-time').value;
    
    if (!date || !time) {
        showToast('Выберите дату и время', 'error');
        return;
    }
    
    try {
        const result = await apiPost('/bookings/manual', {
            client_name: name,
            client_phone: phone || null,
            instructor_id: instructorId || null,
            service_type: service,
            location: location,
            transmission: transmission,
            booking_date: date,
            start_time: time,
        });
        
        if (result && result.ok) {
            if (result.offline) {
                await addQueuedBookingToSnapshot({
                    client_name: name || phone || 'Клиент', client_phone: phone,
                    instructor_id: instructorId || null, service_type: service,
                    instructor_name: manualBookingInstructors.find(i => String(i.id) === String(instructorId))?.name || 'Назначается',
                    transmission, booking_date: date, start_time: time,
                }, result.queued_operation_id, result.local_client_id);
            }
            showToast(result.offline ? 'Запись сохранена офлайн и будет синхронизирована автоматически' : 'Запись создана');
            hideManualBookingForm();
            // Сбрасываем форму
            document.getElementById('mb-name').value = '';
            document.getElementById('mb-phone').value = '';
            hideManualClientSuggestions();
            document.getElementById('mb-date').value = '';
            document.getElementById('mb-time').value = '09:00';
            document.getElementById('mb-location').value = 'Циолковского 30';
            document.getElementById('slots-panel').innerHTML = '<div class="slots-list"><p style="color:var(--text-secondary);text-align:center;padding:24px 0;font-size:13px">Выберите дату</p></div>';
            // Перезагружаем список записей
            loadBookings();
        } else {
            showToast('Ошибка при создании записи', 'error');
        }
    } catch (e) {
        showToast(e.message || 'Ошибка при создании записи', 'error');
    }
}

async function addQueuedBookingToSnapshot(booking, operationId, localClientId) {
    const snapshot = await offlineRead('api-cache', 'offline-snapshot');
    if (!snapshot) return;
    const instructor = snapshot.instructors?.find(i => String(i.id) === String(booking.instructor_id));
    let client = snapshot.clients?.find(item => String(item.id) === String(localClientId));
    if (!client) {
        client = {
            id: localClientId, name: booking.client_name || booking.client_phone || 'Клиент',
            phone: booking.client_phone || null, bookings_count: 0,
            packages: [], certificates: [], created_at: new Date().toISOString(),
        };
        snapshot.clients ||= [];
        snapshot.clients.unshift(client);
    }
    client.bookings_count = Number(client.bookings_count || 0) + 1;
    const localBooking = {
        id: `offline-${operationId}`, source: 'offline', client_name: booking.client_name,
        client_phone: booking.client_phone, instructor_name: booking.instructor_name || instructor?.name || 'Назначается',
        client_id: client.id, instructor_id: booking.instructor_id, service_type: booking.service_type,
        transmission: booking.transmission, location: 'Циолковского 30', date: booking.booking_date,
        start_time: booking.start_time, end_time: formatMinutes(minutesFromTime(booking.start_time) + (
            booking.service_type === 'exam'
                ? (snapshot.slot_rules?.exam_duration_minutes || 20)
                : (snapshot.slot_rules?.training_duration_minutes || 60)
        )),
        status: 'confirmed', price: booking.service_type === 'exam' ? 5000 : 10000,
    };
    snapshot.bookings.push(localBooking);
    await offlineStore('api-cache', 'offline-snapshot', snapshot);
    await offlineStore('api-cache', '/bookings', snapshot.bookings);
    await offlineStore('api-cache', '/clients', snapshot.clients);
}


// --- Calendar for Days Off ---
let currentCalendarMonth = new Date().getMonth();
let currentCalendarYear = new Date().getFullYear();

function renderCalendar() {
    const monthNames = [
        'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
        'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'
    ];
    
    const grid = document.getElementById('calendar-days-grid');
    const title = document.getElementById('calendar-title');
    
    if (!grid || !title) return;
    
    grid.innerHTML = '';
    title.textContent = `${monthNames[currentCalendarMonth]} ${currentCalendarYear}`;
    
    // Количество дней в месяце
    const daysInMonth = new Date(currentCalendarYear, currentCalendarMonth + 1, 0).getDate();
    
    // Первый день месяца (0 = воскресенье, 1 = понедельник, ...)
    const firstDayOfMonth = new Date(currentCalendarYear, currentCalendarMonth, 1).getDay();
    
    // Конвертируем в понедельник = 0
    const firstDay = firstDayOfMonth === 0 ? 6 : firstDayOfMonth - 1;
    
    // Сегодня
    const today = new Date();
    const todayDay = today.getDate();
    const todayMonth = today.getMonth();
    const todayYear = today.getFullYear();
    
    // Добавляем пустые ячейки до первого дня
    for (let i = 0; i < firstDay; i++) {
        const emptyBtn = document.createElement('button');
        emptyBtn.type = 'button';
        emptyBtn.className = 'calendar-day-btn empty';
        emptyBtn.disabled = true;
        grid.appendChild(emptyBtn);
    }
    
    // Создаём кнопки для каждого дня
    for (let day = 1; day <= daysInMonth; day++) {
        const date = new Date(currentCalendarYear, currentCalendarMonth, day);
        const dateStr = formatDateISO(date);
        
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'calendar-day-btn';
        btn.textContent = day;
        
        // Сегодня
        if (day === todayDay && currentCalendarMonth === todayMonth && currentCalendarYear === todayYear) {
            btn.classList.add('today');
        }
        
        // Прошедшая дата
        const isPast = date < today && !(day === todayDay && currentCalendarMonth === todayMonth && currentCalendarYear === todayYear);
        if (isPast) {
            btn.classList.add('past');
            btn.disabled = true;
        }
        
        // Выходной день
        const schedule = instructorDailySchedules.get(dateStr);
        if (schedule?.is_day_off || (!schedule && selectedDaysOff.has(dateStr))) {
            btn.classList.add('day-off');
        }
        if (schedule && !schedule.is_day_off) {
            btn.classList.add('custom-schedule');
            btn.title = 'Особый график';
        }
        
        // Клик по дню
        if (!isPast) {
            // Selecting a day only opens its editor. The former redesign
            // accidentally toggled the day off on every click.
            btn.onclick = () => fillDailyScheduleForm(dateStr);
        }
        
        grid.appendChild(btn);
    }
    
    // Обновляем активную кнопку месяца
    document.querySelectorAll('.calendar-month-btn').forEach(btn => {
        btn.classList.remove('active');
        if (parseInt(btn.dataset.month) === currentCalendarMonth) {
            btn.classList.add('active');
        }
    });
}

function fillDailyScheduleForm(dateStr) {
    const schedule = instructorDailySchedules.get(dateStr);
    document.getElementById('daily-schedule-empty').classList.add('hidden');
    document.getElementById('daily-schedule-panel').classList.remove('hidden');
    document.getElementById('daily-schedule-date').value = dateStr;
    document.getElementById('daily-schedule-title').textContent = formatDisplayDate(dateStr);
    document.getElementById('daily-work-start').value = (schedule?.working_hours_start || '').slice(0, 5);
    document.getElementById('daily-work-end').value = (schedule?.working_hours_end || '').slice(0, 5);
    document.getElementById('daily-lunch-start').value = (schedule?.lunch_start || '').slice(0, 5);
    document.getElementById('daily-lunch-end').value = (schedule?.lunch_end || '').slice(0, 5);
    if (schedule?.is_day_off || (!schedule && selectedDaysOff.has(dateStr))) setDailyScheduleMode('off');
    else if (schedule) setDailyScheduleMode('custom');
    else setDailyScheduleMode('default');
}

function formatDisplayDate(dateStr) {
    const [year, month, day] = dateStr.split('-');
    return `${day}.${month}.${year}`;
}

function selectMonth(month) {
    currentCalendarMonth = month;
    renderCalendar();
}

// Инициализация кнопок выбора месяца и глобальных слушателей
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.calendar-month-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            selectMonth(parseInt(btn.dataset.month));
        });
    });

    const globalSearch = document.getElementById('global-search-input');
    if (globalSearch) {
        globalSearch.addEventListener('input', (e) => {
            const val = e.target.value.trim();
            if (val.length > 0) {
                navigateTo('clients');
                const clientSearch = document.getElementById('client-search');
                if (clientSearch) {
                    clientSearch.value = val;
                    filterClients();
                }
            }
        });
    }

});

function toggleDayOff(dateStr) {
    if (selectedDaysOff.has(dateStr)) {
        selectedDaysOff.delete(dateStr);
        if (instructorDailySchedules.get(dateStr)?.is_day_off) instructorDailySchedules.delete(dateStr);
        setDailyScheduleMode('default');
    } else {
        selectedDaysOff.add(dateStr);
        instructorDailySchedules.set(dateStr, {schedule_date: dateStr, is_day_off: true});
        setDailyScheduleMode('off');
    }
    renderCalendar();
    fillDailyScheduleForm(dateStr);
}

function formatDateISO(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
}

// Экспорт ВСЕХ функций в глобальный scope для Vite
window.exportFullBackup = exportFullBackup;
window.showRestoreBackupDialog = showRestoreBackupDialog;
window.handleRestoreFile = handleRestoreFile;
window.showInstructorForm = showInstructorForm;
window.hideInstructorForm = hideInstructorForm;
window.saveInstructor = saveInstructor;
window.saveDailySchedule = saveDailySchedule;
window.setDailyScheduleMode = setDailyScheduleMode;
window.clearDailyScheduleForm = clearDailyScheduleForm;
window.editInstructor = editInstructor;
window.toggleInstructorActive = toggleInstructorActive;
window.deleteInstructor = deleteInstructor;
window.addVehicle = addVehicle;
window.editVehicle = editVehicle;
window.saveVehicle = saveVehicle;
window.deleteVehicle = deleteVehicle;
window.toggleVehicleRepair = toggleVehicleRepair;
window.showInstructorWeek = showInstructorWeek;
window.closeInstructorWeekPanel = closeInstructorWeekPanel;
window.showClientForm = showClientForm;
window.hideClientForm = hideClientForm;
window.saveClient = saveClient;
window.editClient = editClient;
window.deleteClient = deleteClient;
window.toggleClientHistory = toggleClientHistory;
window.selectClient = selectClient;
window.filterClients = filterClients;
window.showManualBookingForm = showManualBookingForm;
window.hideManualBookingForm = hideManualBookingForm;
window.saveManualBooking = saveManualBooking;
window.searchManualClients = searchManualClients;
window.selectManualClient = selectManualClient;
window.loadSlots = loadSlots;
window.selectSlot = selectSlot;
window.createFaq = createFaq;
window.editFaq = editFaq;
window.deleteFaq = deleteFaq;
window.deleteDialog = deleteDialog;
window.navigateTo = navigateTo;
window.exportBookings = exportBookings;
window.exportClients = exportClients;
window.loadArchive = loadArchive;
window.loadArchivedAudit = loadArchivedAudit;
window.loadArchivedEvents = loadArchivedEvents;
window.setRevenuePeriod = setRevenuePeriod;
window.loadBookings = loadBookings;
window.deleteBooking = deleteBooking;
window.purgeCancelledBooking = purgeCancelledBooking;
window.purgeAllCancelledBookings = purgeAllCancelledBookings;
window.openClientChat = openClientChat;
window.editBooking = editBooking;
window.switchBookingTab = switchBookingTab;
window.toggleApplicationSound = toggleApplicationSound;
window.openOfflineIssues = openOfflineIssues;
window.confirmCancellation = confirmCancellation;
window.rejectCancellation = rejectCancellation;
window.resolveRescheduleRequest = resolveRescheduleRequest;
window.createCertificate = createCertificate;
window.deleteCertificate = deleteCertificate;
window.changePassword = changePassword;
window.sendReply = sendReply;
window.closeSupportChat = closeSupportChat;
window.openDialog = openDialog;
window.switchSupportChannel = switchSupportChannel;
window.closeModal = closeModal;
window.renderCalendar = renderCalendar;
window.selectMonth = selectMonth;
window.toggleDayOff = toggleDayOff;
window.applyCertificate = applyCertificate;
window.handleServiceChange = handleServiceChange;
window.updateManualTransmissionFromInstructor = updateManualTransmissionFromInstructor;

// ==================== ПОДТВЕРЖДЕНИЕ / ОТКЛОНЕНИЕ ЗАЯВОК ====================

async function confirmBooking(bookingId) {
    if (!confirm('Подтвердить эту заявку?')) return;
    try {
        const data = await apiPost(`/bookings/${bookingId}/confirm`, { action: 'confirm' });
        if (data && data.ok) {
            showToast(`Заявка подтверждена! Номер: ${data.booking_number || '—'}`);
        } else {
            showToast('Не удалось подтвердить заявку');
        }
    } catch (e) {
        showToast('Ошибка при подтверждении заявки');
    } finally {
        // Всегда обновляем список, чтобы статус актуализировался без ручного обновления страницы
        loadBookings();
        pollNotificationCounts();
    }
}

async function rejectBooking(bookingId) {
    const rejectionReason = prompt('Причина отклонения для клиента (обязательно):');
    if (rejectionReason === null) return;
    if (!rejectionReason.trim()) {
        showToast('Укажите причину отклонения для клиента', 'error');
        return;
    }
    try {
        const data = await apiPost(`/bookings/${bookingId}/confirm`, {
            action: 'reject', rejection_reason: rejectionReason.trim(),
        });
        if (data && data.ok) {
            showToast('Заявка отклонена');
        } else {
            showToast('Не удалось отклонить заявку');
        }
    } catch (e) {
        showToast('Ошибка при отклонении заявки');
    } finally {
        // Всегда обновляем список, чтобы статус актуализировался без ручного обновления страницы
        loadBookings();
        pollNotificationCounts();
    }
}

async function copyBookingCard(bookingId) {
    const data = await apiGet(`/bookings/${bookingId}/card-text`);
    if (data && data.text) {
        try {
            await navigator.clipboard.writeText(data.text.replace(/<[^>]*>/g, ''));
            showToast('Карточка записи скопирована в буфер обмена');
        } catch (e) {
            prompt('Скопируйте текст:', data.text.replace(/<[^>]*>/g, ''));
        }
    }
}

async function copyBookingReminder(bookingId) {
    const data = await apiGet(`/bookings/${bookingId}/reminder-text`);
    if (data && data.text) {
        try {
            await navigator.clipboard.writeText(data.text);
            showToast('Текст напоминания скопирован в буфер обмена');
        } catch (e) {
            prompt('Скопируйте текст:', data.text);
        }
    }
}

async function copySlotText(bookingId) {
    const data = await apiGet(`/bookings/${bookingId}/copy-text`);
    if (data && data.text) {
        try {
            await navigator.clipboard.writeText(data.text);
            showToast('Текст скопирован в буфер обмена');
        } catch (e) {
            prompt('Скопируйте текст:', data.text);
        }
    }
}

// ==================== ЛИСТ ОЖИДАНИЯ ====================

async function loadWaitingList() {
    const data = await apiGet('/waiting-list');
    if (!data) return;
    // The API calculates cancelled-slot matches in the same response.  This
    // avoids one sequential request for every cancelled booking on tab open.
    const matchingIds = new Set((data.items || [])
        .filter(item => item.matches_cancelled_slot)
        .map(item => item.id));
    const hasWaitingAttention = (data.items || [])
        .some(item => item.requires_attention || item.matches_cancelled_slot);
    if (hasWaitingAttention && currentBookingTab !== 'waiting-list') {
        waitingAttentionAcknowledged = false;
    }
    if (currentBookingTab !== 'waiting-list') updateWaitingAttention(hasWaitingAttention);
    const container = document.getElementById('waiting-list-container');
    if (!container) return;
    if (!data.items || !data.items.length) {
        container.innerHTML = '<p style="color:var(--text-secondary);text-align:center;padding:32px">Лист ожидания пуст</p>';
        return;
    }
    container.innerHTML = data.items.map(e => {
        const dateStr = e.desired_date ? e.desired_date : 'Любая дата';
        const timeStr = e.desired_time_start ? `${e.desired_time_start}${e.desired_time_end ? ' - ' + e.desired_time_end : ''}` : 'Любое время';
        const transStr = e.transmission ? (e.transmission === 'manual' ? 'МКПП' : e.transmission === 'automatic' ? 'АКПП' : e.transmission) : 'Любая КПП';
        const statusIcon = e.status === 'not_answered' ? '\ud83d\udcde\u274c ' : '';
        const instrName = e.instructor_name ? e.instructor_name : 'Любой инструктор';
        const genderStr = e.instructor_gender === 'male' ? 'Мужчина' : e.instructor_gender === 'female' ? 'Женщина' : 'Не важно';
        const sourceBadge = e.client_source === 'telegram' ? '<span class="badge badge-info" title="Telegram-бот">✈️ Telegram</span>' : e.client_source === 'mobile' ? '<span class="badge badge-primary" title="APK-приложение">📱 APK</span>' : '';
        const writeAction = e.client_source === 'telegram' && e.client_id
            ? `<button class="btn btn-primary btn-sm" onclick="event.stopPropagation();openClientChat(${e.client_id})">💬 Написать</button>`
            : `<button class="btn btn-outline btn-sm" onclick="event.stopPropagation();writeWaitingClient(${e.id},'${escapeHtml(e.phone || '')}')">✉️ Связаться</button>`;
        const isOpen = openWaitingEntryId === e.id;
        const matchedStyle = matchingIds.has(e.id) ? 'border:2px solid #f59e0b;background:#fffbeb;box-shadow:0 0 0 3px rgba(245,158,11,.18)' : '';
        const matchedHint = e.requires_attention
            ? '<div style="color:#b45309;font-weight:700;margin:6px 0">🔔 Сегодня нужно связаться с клиентом</div>'
            : matchingIds.has(e.id) ? '<div style="color:#b45309;font-weight:700;margin:6px 0">🔔 Подходит к освободившемуся слоту</div>' : '';
        return `
        <div id="waiting-entry-${e.id}" class="mobile-card waiting-entry ${matchingIds.has(e.id) ? 'waiting-entry-match' : ''}" style="margin-bottom:8px;${matchedStyle}" onclick="toggleWaitingEntry(${e.id}, event)">
            <div class="mobile-card-header waiting-entry-summary">
                <div>
                    <div class="mobile-card-title">${statusIcon}${escapeHtml(e.name)}</div>
                    <div style="font-size:12px;color:var(--text-secondary)">${e.phone || '—'} · ${dateStr} · ${timeStr} · ${transStr}</div>
                </div>
                <div style="display:flex;align-items:center;gap:8px">${sourceBadge}<span class="badge badge-${e.status === 'waiting' ? 'primary' : 'secondary'}">${e.status === 'waiting' ? 'Ожидает' : e.status === 'not_answered' ? 'Не ответил' : e.status === 'refused' ? 'Отказался' : e.status}</span><span class="waiting-entry-arrow">${isOpen ? '⌃' : '⌄'}</span></div>
            </div>
            <div class="waiting-entry-details ${isOpen ? '' : 'hidden'}">
            <div class="mobile-card-row"><span class="mobile-card-label">Дата:</span><span class="mobile-card-value">${dateStr}</span></div>
            <div class="mobile-card-row"><span class="mobile-card-label">Время:</span><span class="mobile-card-value">${timeStr}</span></div>
            <div class="mobile-card-row"><span class="mobile-card-label">КПП:</span><span class="mobile-card-value">${transStr}</span></div>
            <div class="mobile-card-row"><span class="mobile-card-label">Инструктор:</span><span class="mobile-card-value">${escapeHtml(instrName)}</span></div>
            <div class="mobile-card-row"><span class="mobile-card-label">Пол инструктора:</span><span class="mobile-card-value">${genderStr}</span></div>
            ${matchedHint}
            ${e.notes ? `<div class="mobile-card-row"><span class="mobile-card-label">Заметки:</span><span class="mobile-card-value">${escapeHtml(e.notes)}</span></div>` : ''}
            <div style="display:flex;gap:4px;margin-top:8px;flex-wrap:wrap">
                <button class="btn btn-outline btn-sm" onclick="editWaitingEntry(${e.id})">\u270f\ufe0f Ред.</button>
                ${writeAction}
                <button class="btn btn-outline btn-sm" onclick="setWaitingStatus(${e.id},'not_answered')">\ud83d\udcde\u274c Не ответил</button>
                <button class="btn btn-outline btn-sm" onclick="setWaitingStatus(${e.id},'refused')">Отказался</button>
                <button class="btn btn-outline-danger btn-sm" onclick="deleteWaitingEntry(${e.id})">\ud83d\uddd1\ufe0f</button>
            </div>
            </div>
        </div>`;
    }).join('');
}

function toggleWaitingEntry(entryId, event) {
    if (event?.target?.closest('button')) return;
    const previousId = openWaitingEntryId;
    openWaitingEntryId = openWaitingEntryId === entryId ? null : entryId;
    // Do not fetch bookings/matches again merely to open a card. The old
    // implementation made one click wait for every cancelled-slot request.
    for (const card of document.querySelectorAll('.waiting-entry')) {
        const details = card.querySelector('.waiting-entry-details');
        const isCurrent = card.id === `waiting-entry-${openWaitingEntryId}`;
        details?.classList.toggle('hidden', !isCurrent);
        const arrow = card.querySelector('.waiting-entry-arrow');
        if (arrow) arrow.textContent = isCurrent ? '⌃' : '⌄';
    }
}

function updateWaitingAttention(hasMatches) {
    const tab = document.getElementById('tab-waiting-list');
    if (!tab) return;
    const pulse = Boolean(hasMatches) && !waitingAttentionAcknowledged && currentBookingTab !== 'waiting-list';
    tab.classList.toggle('waiting-tab-attention', pulse);
    tab.title = pulse ? 'Есть клиенты, подходящие к освободившемуся слоту' : '';
}

async function refreshWaitingAttention() {
    const waiting = await apiGet('/waiting-list').catch(() => ({ items: [] }));
    if ((waiting?.items || []).some(item => item.requires_attention || item.matches_cancelled_slot)) {
        waitingAttentionAcknowledged = false;
        updateWaitingAttention(true);
        return;
    }
    updateWaitingAttention(false);
}

async function refreshConflictsAttention() {
    const data = await apiGet('/bookings/conflicts').catch(() => ({ groups: [] }));
    const count = data.groups?.length || 0;
    if (count > 0 && lastKnownConflictsCount === 0) {
        conflictsAttentionAcknowledged = false;
    }
    lastKnownConflictsCount = count;
    if (count > 0) {
        updateConflictsAttention(true);
    } else {
        updateConflictsAttention(false);
    }
}

async function setWaitingStatus(entryId, status) {
    await apiPut(`/waiting-list/${entryId}/status`, { action: status });
    loadWaitingList();
}

async function deleteWaitingEntry(entryId) {
    if (!confirm('Удалить из листа ожидания?')) return;
    await apiDelete(`/waiting-list/${entryId}`);
    loadWaitingList();
}

async function editWaitingEntry(entryId) {
    const data = await apiGet('/waiting-list');
    if (!data || !data.items) return;
    const entry = data.items.find(e => e.id === entryId);
    if (!entry) return;
    showWaitingEntryForm(entry);
}

function writeWaitingClient(entryId, phone) {
    if (!phone) {
        showToast('У клиента нет номера телефона', 'error');
        return;
    }
    navigateTo('support');
    setTimeout(() => {
        showToast(`Откройте диалог клиента с номером ${phone}; если это WhatsApp или звонок — свяжитесь по номеру.`);
    }, 500);
}

// ==================== CONFLICT RESOLUTION ====================

function updateConflictsAttention(hasConflicts) {
    const tab = document.getElementById('tab-conflicts');
    if (!tab) return;
    const pulse = Boolean(hasConflicts) && !conflictsAttentionAcknowledged && currentBookingTab !== 'conflicts';
    tab.classList.toggle('conflict-tab-attention', pulse);
    tab.title = pulse ? 'Есть конфликтные записи, требующие решения' : '';
}

async function checkPendingConflicts() {
    showToast('Проверяю pending-заявки на конфликты...');
    const result = await apiPost('/bookings/check-pending-conflicts', {});
    if (!result) return;
    if (result.merged_count > 0) {
        showToast(`✅ ${result.merged_count} заявок объединено с ручными записями`);
    }
    if (result.conflicts_count > 0) {
        showToast(`⚠️ ${result.conflicts_count} конфликтов найдено`, 'warning');
        updateConflictsAttention(true);
    } else {
        showToast('Конфликтов нет');
        updateConflictsAttention(false);
    }
    loadConflicts();
}

async function loadConflicts() {
    const container = document.getElementById('conflicts-container');
    if (!container) return;
    container.innerHTML = '<p style="color:#888">Загрузка...</p>';

    const data = await apiGet('/bookings/conflicts');
    const syncErrors = (await offlineOperations().catch(() => [])).filter(operation => operation.sync_error);
    const groups = data?.groups || [];
    if (!groups.length && !syncErrors.length) {
        container.innerHTML = '<div style="text-align:center;padding:40px;color:#888"><p style="font-size:16px">✅ Нет конфликтных записей</p><p style="font-size:13px;margin-top:8px">Все записи в порядке</p></div>';
        updateConflictsAttention(false);
        return;
    }

    if (groups.length) updateConflictsAttention(true);
    let html = syncErrors.map(operation => `
        <div class="conflict-group">
            <div class="conflict-group-header"><span>⚠️ Ошибка синхронизации</span></div>
            <div class="conflict-group-body"><div class="conflict-booking">
                <div class="conflict-booking-client">${escapeHtml(operation.path || 'Офлайн-операция')}</div>
                <div class="conflict-booking-reason">${escapeHtml(operation.sync_error)}</div>
                <div class="conflict-booking-details">Операция будет повторена автоматически после исправления причины на сервере.</div>
            </div></div>
        </div>
    `).join('');
    for (const group of groups) {
        const slot = group.slot;
        html += `<div class="conflict-group" data-slot="${slot.instructor_id}-${slot.date}-${slot.time}">`;
        html += `<div class="conflict-group-header"><span>📍 ${slot.instructor_name} — ${slot.date} в ${slot.time}</span><span style="font-size:12px;font-weight:400">${group.count} записей</span></div>`;
        html += `<div class="conflict-group-body">`;
        for (const b of group.bookings) {
            const isManual = b.source === 'admin' || b.source === 'admin_offline';
            const serviceLabel = b.service_type === 'training' ? 'Обучение' : 'Экзамен';
            const transLabel = b.transmission === 'manual' ? 'Механика' : 'Автомат';
            html += `<div class="conflict-booking unselected" id="conflict-booking-${b.id}" data-id="${b.id}">`;
            html += `<div class="conflict-booking-info">`;
            html += `<div class="conflict-booking-client">${escapeHtml(b.client_name)}${isManual ? '<span class="conflict-booking-manual">РУЧНАЯ</span>' : ''}</div>`;
            html += `<div class="conflict-booking-details">📞 ${escapeHtml(b.client_phone)} | 🚗 ${serviceLabel} (${transLabel}) | 📍 ${escapeHtml(b.location)}</div>`;
            html += `<div class="conflict-booking-details">🕐 ${b.start_time}—${b.end_time} | Источник: ${b.source || '—'}</div>`;
            if (b.conflict_reason) {
                html += `<div class="conflict-booking-reason">💬 ${b.conflict_reason}</div>`;
            }
            html += `</div>`;
            html += `<div class="conflict-booking-actions">`;
            html += `<button class="btn btn-success btn-sm" onclick="resolveConflictBooking(${b.id}, 'confirm')" title="Подтвердить эту запись">✅ Подтвердить</button>`;
            html += `<button class="btn btn-outline-danger btn-sm" onclick="resolveConflictBooking(${b.id}, 'reject')" title="Отменить эту запись">❌ Отменить</button>`;
            html += `</div>`;
            html += `</div>`;
        }
        html += `</div></div>`;
    }
    container.innerHTML = html;
}

async function resolveConflictBooking(bookingId, action) {
    const actionLabel = action === 'confirm' ? 'подтвердить' : 'отменить';
    let rejectionReason = null;
    if (action === 'reject') {
        rejectionReason = prompt('Причина отклонения для клиента (обязательно):');
        if (rejectionReason === null) return;
        if (!rejectionReason.trim()) {
            showToast('Укажите причину отклонения для клиента', 'error');
            return;
        }
    }
    if (!confirm(`Вы уверены, что хотите ${actionLabel} эту запись?`)) return;

    try {
        const result = await apiPost(`/bookings/${bookingId}/confirm`, {
            action,
            ...(rejectionReason ? { rejection_reason: rejectionReason.trim() } : {}),
        });
        if (!result || !result.ok) {
            showToast('Ошибка при рассмотрении конфликта', 'error');
            return;
        }
        showToast(`Запись ${action === 'confirm' ? 'подтверждена' : 'отклонена'}`);
        loadConflicts();
    } catch (error) {
        showToast(error.message || 'Ошибка при рассмотрении конфликта', 'error');
    }
}

window.checkPendingConflicts = checkPendingConflicts;
window.resolveConflictBooking = resolveConflictBooking;
window.loadConflicts = loadConflicts;

window.confirmBooking = confirmBooking;
window.rejectBooking = rejectBooking;
window.copyBookingCard = copyBookingCard;
window.copyBookingReminder = copyBookingReminder;
window.copySlotText = copySlotText;
window.loadWaitingList = loadWaitingList;
window.toggleWaitingEntry = toggleWaitingEntry;
window.setWaitingStatus = setWaitingStatus;
window.deleteWaitingEntry = deleteWaitingEntry;
window.editWaitingEntry = editWaitingEntry;
window.writeWaitingClient = writeWaitingClient;
window.deletePackage = deletePackage;
window.addWaitingClient = addWaitingClient;
window.resolveCertificateRequest = resolveCertificateRequest;

async function addWaitingClient() {
    showWaitingEntryForm();
}

async function showWaitingEntryForm(entry = null) {
    const modal = document.getElementById('app-modal');
    const title = document.getElementById('modal-title');
    const body = document.getElementById('modal-body');
    const save = document.getElementById('modal-save-btn');
    let instructors = [];
    try { instructors = await apiGet('/instructors') || []; }
    catch (_) { showToast('Не удалось загрузить инструкторов: можно сохранить запись без выбора инструктора', 'error'); }
    const selectedInstructor = entry?.instructor_id || '';
    title.textContent = entry ? 'Редактировать клиента в листе ожидания' : 'Добавить клиента в лист ожидания';
    body.innerHTML = `
      <form id="waiting-entry-form" class="form-grid" onsubmit="return false">
        <div class="form-group"><label>Имя клиента *</label><input class="form-control" id="wl-name" value="${escapeHtml(entry?.name || '')}" required></div>
        <div class="form-group"><label>Телефон</label><input class="form-control" id="wl-phone" type="tel" value="${escapeHtml(entry?.phone || '')}" placeholder="+7 700 000 00 00"></div>
        <div class="form-group"><label>Желаемая дата</label><input class="form-control" id="wl-date" type="date" value="${entry?.desired_date || ''}"><small>Оставьте пустым для ближайшего свободного времени.</small></div>
        <div class="form-row" style="display:grid;grid-template-columns:1fr 1fr;gap:12px"><div class="form-group"><label>Время от</label><input class="form-control" id="wl-time-start" type="time" value="${entry?.desired_time_start || ''}"></div><div class="form-group"><label>Время до</label><input class="form-control" id="wl-time-end" type="time" value="${entry?.desired_time_end || ''}"></div></div>
        <div class="form-row" style="display:grid;grid-template-columns:1fr 1fr;gap:12px"><div class="form-group"><label>КПП</label><select class="form-control" id="wl-transmission"><option value="">Любая</option><option value="automatic" ${entry?.transmission === 'automatic' ? 'selected' : ''}>АКПП</option><option value="manual" ${entry?.transmission === 'manual' ? 'selected' : ''}>МКПП</option></select></div><div class="form-group"><label>Пол инструктора</label><select class="form-control" id="wl-gender"><option value="">Не важно</option><option value="male" ${entry?.instructor_gender === 'male' ? 'selected' : ''}>Мужчина</option><option value="female" ${entry?.instructor_gender === 'female' ? 'selected' : ''}>Женщина</option></select></div></div>
        <div class="form-group"><label>Инструктор</label><select class="form-control" id="wl-instructor"><option value="">Любой инструктор</option>${instructors.filter(i => i.is_active !== false).map(i => `<option value="${i.id}" ${String(i.id) === String(selectedInstructor) ? 'selected' : ''}>${escapeHtml(i.name)}</option>`).join('')}</select></div>
        <div class="form-group"><label>Заметки</label><textarea class="form-control" id="wl-notes" rows="3" placeholder="Например: удобнее написать в WhatsApp">${escapeHtml(entry?.notes || '')}</textarea></div>
      </form>`;
    save.textContent = entry ? 'Сохранить изменения' : 'Добавить клиента';
    save.style.display = 'block';
    save.onclick = async () => {
        const payload = {
            name: document.getElementById('wl-name').value.trim(), phone: document.getElementById('wl-phone').value.trim() || null,
            desired_date: document.getElementById('wl-date').value || null, desired_time_start: document.getElementById('wl-time-start').value || null,
            desired_time_end: document.getElementById('wl-time-end').value || null, transmission: document.getElementById('wl-transmission').value || null,
            instructor_gender: document.getElementById('wl-gender').value || null, instructor_id: Number(document.getElementById('wl-instructor').value) || null,
            notes: document.getElementById('wl-notes').value.trim() || null,
        };
        if (!payload.name) { showToast('Укажите имя клиента', 'error'); return; }
        if (payload.desired_time_start && payload.desired_time_end && payload.desired_time_end <= payload.desired_time_start) { showToast('Время «до» должно быть позже времени «от»', 'error'); return; }
        try {
            if (entry) await apiPut(`/waiting-list/${entry.id}`, payload); else await apiPost('/waiting-list', payload);
            closeModal(); showToast(entry ? 'Запись обновлена' : 'Клиент добавлен в лист ожидания'); loadWaitingList();
        } catch (e) { showToast(e.message, 'error'); }
    };
    modal.classList.remove('hidden');
}
