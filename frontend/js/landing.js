/* GREENTIME — лендинг. Без зависимостей: анимации, калькулятор, FAQ, чат. */
(function () {
    'use strict';

    var TELEGRAM_URL = 'https://t.me/green_time_drive_bot';
    var PHONE_TEXT = '+7 707 881 08 48';
    var PRICE_PER_HOUR = 7000;

    var FAQ_FALLBACK = [
        { question: 'Сколько стоит занятие?', answer: 'Один час практики стоит 7 000 ₸. Других платежей нет.' },
        { question: 'Где проходят занятия?', answer: 'На площадке по адресу Лесная 5 в Павлодаре.' },
        { question: 'Можно выбрать механику или автомат?', answer: 'Да. Автомобили есть и с МКПП, и с АКПП — коробку выбираете при записи.' },
        { question: 'Как записаться?', answer: 'Через Telegram-бот ' + TELEGRAM_URL + ' или по телефону ' + PHONE_TEXT + '.' },
        { question: 'В какое время можно заниматься?', answer: 'Ежедневно с 8:00 до 22:00. Конкретное время выбираете при записи.' },
        { question: 'Сколько занятий нужно?', answer: 'Зависит от подготовки: кому-то хватает нескольких занятий, кому-то нужно больше практики. Инструктор скажет, когда вы готовы.' }
    ];

    var INSTRUCTOR_FALLBACK = [
        { name: 'Инструктор по механике', transmission: 'МКПП', experience_years: 8, description: 'Спокойно объясняет работу сцепления и учит чувствовать автомобиль.' },
        { name: 'Инструктор по автомату', transmission: 'АКПП', experience_years: 6, description: 'Городской маршрут, парковка и уверенность в плотном потоке.' },
        { name: 'Универсальный инструктор', transmission: 'МКПП и АКПП', experience_years: 10, description: 'Индивидуальная программа под ваш уровень подготовки.' }
    ];

    function formatMoney(value) {
        return String(Math.round(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    }

    function pluralHours(n) {
        var mod10 = n % 10;
        var mod100 = n % 100;
        if (mod10 === 1 && mod100 !== 11) return n + ' час';
        if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return n + ' часа';
        return n + ' часов';
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    /* ── Появление блоков при прокрутке ───────────────────────────────────── */

    var revealObserver = null;

    function makeObserver() {
        if (!('IntersectionObserver' in window)) return null;
        return new IntersectionObserver(function (entries, observer) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                var el = entry.target;
                var delay = parseInt(el.getAttribute('data-delay') || '0', 10);
                window.setTimeout(function () { el.classList.add('is-visible'); }, delay);
                observer.unobserve(el);
            });
        }, { threshold: 0.12, rootMargin: '0px 0px -60px 0px' });
    }

    function initReveal() {
        var items = Array.prototype.slice.call(document.querySelectorAll('.reveal'));
        if (!items.length) return;

        revealObserver = makeObserver();

        if (!revealObserver) {
            items.forEach(function (el) { el.classList.add('is-visible'); });
            return;
        }

        items.forEach(function (el) { revealObserver.observe(el); });
    }

    function observeNew(container) {
        if (!revealObserver) {
            container.querySelectorAll('.reveal').forEach(function (el) {
                el.classList.add('is-visible');
            });
            return;
        }
        container.querySelectorAll('.reveal').forEach(function (el) {
            revealObserver.observe(el);
        });
    }

    /* ── Шапка и индикатор прокрутки ──────────────────────────────────────── */

    function initScrollUi() {
        var header = document.getElementById('site-header');
        var progress = document.getElementById('progress');
        var ticking = false;

        function update() {
            var top = window.scrollY || window.pageYOffset || 0;
            if (header) header.classList.toggle('is-scrolled', top > 12);

            if (progress) {
                var height = document.documentElement.scrollHeight - window.innerHeight;
                var ratio = height > 0 ? Math.min(top / height, 1) : 0;
                progress.style.width = (ratio * 100) + '%';
            }
            ticking = false;
        }

        window.addEventListener('scroll', function () {
            if (ticking) return;
            ticking = true;
            window.requestAnimationFrame(update);
        }, { passive: true });

        update();
    }

    /* ── Меню ─────────────────────────────────────────────────────────────── */

    function initMenu() {
        var burger = document.getElementById('burger');
        var nav = document.getElementById('nav');
        if (!burger || !nav) return;

        function close() {
            nav.classList.remove('is-open');
            burger.setAttribute('aria-expanded', 'false');
        }

        burger.addEventListener('click', function () {
            var open = nav.classList.toggle('is-open');
            burger.setAttribute('aria-expanded', open ? 'true' : 'false');
        });

        nav.addEventListener('click', function (event) {
            if (event.target.closest('.nav__link')) close();
        });

        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') close();
        });

        window.addEventListener('resize', function () {
            if (window.innerWidth > 760) close();
        });
    }

    /* ── Калькулятор ──────────────────────────────────────────────────────── */

    var LESSON_DISCOUNT = 500;

    function initCalculator() {
        var range = document.getElementById('hours');
        var hoursOut = document.getElementById('hours-out');
        var sumOut = document.getElementById('calc-sum');
        var saveOut = document.getElementById('calc-save');
        if (!range || !hoursOut || !sumOut) return;

        function render() {
            var value = parseInt(range.value, 10) || 1;
            // Первое занятие по 7 000 ₸, каждое следующее — на 500 ₸ дешевле.
            var save = (value - 1) * LESSON_DISCOUNT;
            var total = value * PRICE_PER_HOUR - save;

            hoursOut.textContent = pluralHours(value);
            sumOut.textContent = formatMoney(total) + ' ₸';

            if (saveOut) {
                if (save > 0) {
                    saveOut.textContent = 'скидка ' + formatMoney(save) + ' ₸';
                    saveOut.hidden = false;
                } else {
                    saveOut.hidden = true;
                }
            }

            sumOut.classList.add('is-bump');
            window.setTimeout(function () { sumOut.classList.remove('is-bump'); }, 180);
        }

        range.addEventListener('input', render);
        render();
    }

    /* ── Лёгкий эффект наклона и магнитные кнопки ─────────────────────────── */

    function initTilt() {
        if (window.matchMedia('(max-width: 1040px)').matches) return;
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

        document.querySelectorAll('.tilt').forEach(function (card) {
            var frame = null;

            card.addEventListener('mousemove', function (event) {
                if (frame) return;
                frame = window.requestAnimationFrame(function () {
                    var rect = card.getBoundingClientRect();
                    var x = (event.clientX - rect.left) / rect.width - 0.5;
                    var y = (event.clientY - rect.top) / rect.height - 0.5;
                    card.style.transform =
                        'perspective(900px) rotateX(' + (-y * 6).toFixed(2) + 'deg) ' +
                        'rotateY(' + (x * 8).toFixed(2) + 'deg) translateY(-6px)';
                    frame = null;
                });
            });

            card.addEventListener('mouseleave', function () {
                if (frame) {
                    window.cancelAnimationFrame(frame);
                    frame = null;
                }
                card.style.transform = '';
            });
        });
    }

    function initMagnetic() {
        if (window.matchMedia('(max-width: 1040px)').matches) return;
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

        document.querySelectorAll('.magnetic').forEach(function (button) {
            var frame = null;

            button.addEventListener('mousemove', function (event) {
                if (frame) return;
                frame = window.requestAnimationFrame(function () {
                    var rect = button.getBoundingClientRect();
                    var x = event.clientX - rect.left - rect.width / 2;
                    var y = event.clientY - rect.top - rect.height / 2;
                    button.style.transform =
                        'translate(' + (x * 0.12).toFixed(1) + 'px, ' + (y * 0.18 - 2).toFixed(1) + 'px)';
                    frame = null;
                });
            });

            button.addEventListener('mouseleave', function () {
                if (frame) {
                    window.cancelAnimationFrame(frame);
                    frame = null;
                }
                button.style.transform = '';
            });
        });
    }

    /* ── Инструкторы ──────────────────────────────────────────────────────── */

    function renderInstructors(list) {
        var container = document.getElementById('team');
        if (!container) return;

        container.innerHTML = list.map(function (item, index) {
            var name = escapeHtml(item.name || 'Инструктор');
            var spec = escapeHtml(item.transmission || 'МКПП / АКПП');
            var years = Number(item.experience_years) || 0;
            var desc = escapeHtml(item.description || '');

            return (
                '<article class="person reveal" data-delay="' + Math.min(index * 60, 240) + '">' +
                    '<div class="person__top">' +
                        '<span class="person__avatar" aria-hidden="true">🚗</span>' +
                        '<div>' +
                            '<p class="person__name">' + name + '</p>' +
                            '<p class="person__spec">' + spec + '</p>' +
                        '</div>' +
                    '</div>' +
                    (years ? '<p class="person__meta">Стаж: ' + years + ' лет</p>' : '') +
                    (desc ? '<p class="person__desc">' + desc + '</p>' : '') +
                '</article>'
            );
        }).join('');

        observeNew(container);
    }

    function renderFaq(list) {
        var container = document.getElementById('faq-list');
        if (!container) return;

        container.innerHTML = list.slice(0, 6).map(function (item, index) {
            return (
                '<details class="faq__item reveal" data-delay="' + Math.min(index * 50, 200) + '">' +
                    '<summary class="faq__q">' + escapeHtml(item.question) + '</summary>' +
                    '<p class="faq__a">' + escapeHtml(item.answer) + '</p>' +
                '</details>'
            );
        }).join('');

        observeNew(container);
    }

    /* ── Чат ──────────────────────────────────────────────────────────────── */

    function initChat() {
        var win = document.getElementById('chat-window');
        var toggle = document.getElementById('chat-toggle');
        var close = document.getElementById('chat-close');
        var backdrop = document.getElementById('chat-backdrop');
        var form = document.getElementById('chat-form');
        var input = document.getElementById('chat-input');
        var messages = document.getElementById('chat-messages');
        var history = [];
        var started = false;

        if (!win || !toggle || !messages) return;

        function addMessage(role, text) {
            var node = document.createElement('div');
            node.className = 'msg msg--' + role;
            node.textContent = text;
            messages.appendChild(node);
            messages.scrollTop = messages.scrollHeight;
            return node;
        }

        function open() {
            win.hidden = false;
            if (backdrop) backdrop.hidden = false;
            toggle.hidden = true;
            toggle.setAttribute('aria-expanded', 'true');
            // На телефоне шторка занимает экран — страницу под ней не листаем.
            if (window.matchMedia('(max-width: 760px)').matches) {
                document.body.classList.add('chat-open');
            }
            if (!started) {
                started = true;
                addMessage('bot', 'Здравствуйте! Это GREENTIME. Спросите про цену, расписание, площадку или запись.');
            }
            if (input) input.focus();
        }

        function hide() {
            win.hidden = true;
            if (backdrop) backdrop.hidden = true;
            toggle.hidden = false;
            toggle.setAttribute('aria-expanded', 'false');
            document.body.classList.remove('chat-open');
        }

        toggle.addEventListener('click', open);
        if (close) close.addEventListener('click', hide);
        if (backdrop) backdrop.addEventListener('click', hide);

        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && !win.hidden) hide();
        });

        if (!form || !input) return;

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            var text = input.value.trim();
            if (!text) return;

            input.value = '';
            addMessage('user', text);
            history.push({ role: 'user', content: text });

            var pending = addMessage('bot', 'Печатает…');

            fetch('/api/chat/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, history: history.slice(-10) })
            })
                .then(function (response) { return response.json(); })
                .then(function (data) {
                    if (pending) pending.remove();
                    var reply = (data && data.reply) ? data.reply : 'Не удалось получить ответ. Позвоните: ' + PHONE_TEXT;
                    addMessage('bot', reply);
                    history.push({ role: 'assistant', content: reply });
                })
                .catch(function () {
                    if (pending) pending.remove();
                    addMessage('bot', 'Ошибка связи. Позвоните: ' + PHONE_TEXT);
                });
        });
    }

    /* ── Старт ────────────────────────────────────────────────────────────── */

    document.addEventListener('DOMContentLoaded', function () {
        initReveal();
        initScrollUi();
        initMenu();
        initCalculator();
        initTilt();
        initMagnetic();
        initChat();

        fetch('/api/faq')
            .then(function (response) { return response.ok ? response.json() : []; })
            .then(function (items) { renderFaq(items && items.length ? items : FAQ_FALLBACK); })
            .catch(function () { renderFaq(FAQ_FALLBACK); });

        fetch('/api/instructors')
            .then(function (response) { return response.ok ? response.json() : []; })
            .then(function (items) { renderInstructors(items && items.length ? items : INSTRUCTOR_FALLBACK); })
            .catch(function () { renderInstructors(INSTRUCTOR_FALLBACK); });
    });
})();
