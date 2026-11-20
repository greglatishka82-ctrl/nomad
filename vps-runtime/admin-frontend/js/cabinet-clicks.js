(function () {
  'use strict';
  var box = document.getElementById('site-traffic-cabinet');
  if (!box) return;
  var days = 30;
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; }); };
  var num = function (n) { return Number(n || 0).toLocaleString('ru-RU'); };
  var human = function (s) { return ({ direct: 'Прямой / не определён', whatsapp: 'WhatsApp', telegram: 'Telegram', instagram: 'Instagram', tiktok: 'TikTok', yandex: 'Яндекс', google: 'Google', button: 'Кнопка', internal: 'Переход по кабинету', external: 'Внешняя ссылка', mobile: 'Телефон', desktop: 'Компьютер', tablet: 'Планшет' })[s] || s || '—'; };
  function table(head, rows) {
    return '<div class="traffic-table"><table class="data-table"><thead><tr>' + head.map(function (x) { return '<th>' + x + '</th>'; }).join('') + '</tr></thead><tbody>' +
      (rows.length ? rows.map(function (r) { return '<tr>' + r.map(function (x) { return '<td>' + esc(x) + '</td>'; }).join('') + '</tr>'; }).join('') : '<tr><td colspan="' + head.length + '">За этот период данных пока нет</td></tr>') +
      '</tbody></table></div>';
  }
  function shell(content) {
    box.innerHTML = '<div class="traffic-head"><div><h2>Личный кабинет клиента</h2><p>Нажатия кнопок в кабинете: запись, история, карточка и соцсети</p></div><label>Период <select id="cabinet-period">' +
      '<option value="1">Сегодня и последние 24 часа</option><option value="7">7 дней</option><option value="30" selected>30 дней</option><option value="90">90 дней</option><option value="365">365 дней</option>' +
      '</select></label></div><div class="traffic-content">' + content + '</div>';
    var sel = box.querySelector('#cabinet-period');
    if (sel) sel.value = String(days);
  }
  async function load() {
    shell('<p>Загружаем статистику…</p>');
    try {
      var r = await fetch('/api/admin/site-traffic/overview?days=' + days, { credentials: 'include', cache: 'no-store' });
      if (r.status === 401) { box.innerHTML = ''; return; }
      if (!r.ok) throw new Error('Не удалось загрузить статистику');
      var d = await r.json();
      var c = d.cabinet || {};
      var stats = [
        ['Клики в кабинете', c.clicks, '👆', 'visitors', 'нажатий кнопок'],
        ['WhatsApp из кабинета', c.whatsapp, '💬', 'whatsapp', 'нажатий'],
        ['Telegram из кабинета', c.telegram, '✈', 'telegram', 'нажатий'],
        ['Карточка: поделились', c.share, '↗', 'time', 'нажатий по кнопкам карточки']
      ];
      var html = '<div class="traffic-stats">' + stats.map(function (s) {
        return '<div class="stat-card analytics-channel-card traffic-stat ' + s[3] + '"><div class="stat-header"><span class="stat-title">' + s[0] + '</span><span class="stat-icon" aria-hidden="true">' + s[2] + '</span></div><div class="stat-value">' + num(s[1]) + '</div><div class="stat-trend">' + s[4] + '</div></div>';
      }).join('') + '</div>';
      html += '<p class="traffic-note">Это нажатия в личном кабинете клиента, отдельно от основного сайта. Считается сам факт нажатия кнопки, а не то, что клиент сделал дальше.</p>';
      html += '<div class="traffic-grid"><section class="card"><h3>Какие кнопки нажимают в кабинете</h3>' +
        table(['Элемент', 'Тип', 'Нажатия'], (d.clicks_cabinet || []).map(function (x) { return [x.label || 'Без названия', human(x.target), num(x.count)]; })) + '</section></div>';
      shell(html);
    } catch (e) {
      shell('<p>' + esc(e.message) + '</p>');
    }
  }
  box.addEventListener('change', function (e) { if (e.target && e.target.id === 'cabinet-period') { days = Number(e.target.value); load(); } });
  document.querySelectorAll('[data-page="analytics"]').forEach(function (el) {
    el.addEventListener('click', function () { setTimeout(load, 200); });
  });
  var loginForm = document.getElementById('login-form');
  if (loginForm) loginForm.addEventListener('submit', function () { setTimeout(load, 1200); });
  if (document.getElementById('page-analytics') && !document.getElementById('page-analytics').classList.contains('hidden')) load();
})();
