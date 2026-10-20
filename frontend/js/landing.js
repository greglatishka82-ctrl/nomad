// ===== SCROLL ANIMATIONS =====
class ScrollObserver {
    constructor() {
        this.options = {
            threshold: 0.1,
            rootMargin: '0px 0px -100px 0px'
        };
        this.observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const delay = entry.target.dataset.delay || 0;
                    setTimeout(() => {
                        entry.target.classList.add('revealed');
                    }, delay);
                    this.observer.unobserve(entry.target);
                }
            });
        }, this.options);
        
        document.querySelectorAll('.reveal-up').forEach(el => {
            this.observer.observe(el);
        });
    }
}

// ===== COUNTER ANIMATION =====
class Counter {
    constructor(element) {
        this.element = element;
        this.target = parseInt(element.dataset.target);
        this.suffix = element.dataset.suffix || '';
        this.current = 0;
        this.duration = 2000;
        this.stepTime = 20;
        this.steps = this.duration / this.stepTime;
        this.increment = this.target / this.steps;
    }
    
    start() {
        const timer = setInterval(() => {
            this.current += this.increment;
            if (this.current >= this.target) {
                this.current = this.target;
                clearInterval(timer);
            }
            this.element.textContent = Math.floor(this.current) + this.suffix;
        }, this.stepTime);
    }
}

// ===== MAGNETIC BUTTON EFFECT (throttled via requestAnimationFrame) =====
class MagneticButton {
    constructor(element) {
        this.element = element;
        this.strength = 0.3;
        this.rafId = null;
        this.init();
    }
    
    init() {
        this.element.addEventListener('mousemove', (e) => {
            if (this.rafId) return;
            this.rafId = requestAnimationFrame(() => {
                const rect = this.element.getBoundingClientRect();
                const x = e.clientX - rect.left - rect.width / 2;
                const y = e.clientY - rect.top - rect.height / 2;
                this.element.style.transform = `translate(${x * this.strength}px, ${y * this.strength}px)`;
                this.rafId = null;
            });
        });
        
        this.element.addEventListener('mouseleave', () => {
            if (this.rafId) {
                cancelAnimationFrame(this.rafId);
                this.rafId = null;
            }
            this.element.style.transform = 'translate(0, 0)';
        });
    }
}

// ===== TILT CARD EFFECT (throttled via requestAnimationFrame) =====
class TiltCard {
    constructor(element) {
        this.element = element;
        this.maxTilt = 10;
        this.rafId = null;
        this.init();
    }
    
    init() {
        this.element.addEventListener('mousemove', (e) => {
            if (this.rafId) return;
            this.rafId = requestAnimationFrame(() => {
                const rect = this.element.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;
                
                const centerX = rect.width / 2;
                const centerY = rect.height / 2;
                
                const tiltX = (y - centerY) / centerY * this.maxTilt;
                const tiltY = (centerX - x) / centerX * this.maxTilt;
                
                this.element.style.transform = `perspective(1000px) rotateX(${tiltX}deg) rotateY(${tiltY}deg) translateY(-8px)`;
                
                const shine = this.element.querySelector('.card-shine');
                if (shine) {
                    const angle = Math.atan2(y - centerY, x - centerX) * (180 / Math.PI);
                    shine.style.transform = `rotate(${angle}deg)`;
                }
                this.rafId = null;
            });
        });
        
        this.element.addEventListener('mouseleave', () => {
            if (this.rafId) {
                cancelAnimationFrame(this.rafId);
                this.rafId = null;
            }
            this.element.style.transform = 'perspective(1000px) rotateX(0) rotateY(0) translateY(0)';
        });
    }
}

// ===== CHAT FUNCTIONALITY =====
let chatHistory = [];

function toggleChat() {
    const win = document.getElementById('chat-window');
    const btn = document.getElementById('chat-toggle');
    if (win.classList.contains('hidden')) {
        win.classList.remove('hidden');
        btn.style.display = 'none';
        if (!document.getElementById('chat-messages').children.length) {
            addMessage('bot', 'Здравствуйте! Я помощник школы GREENTIME. Спросите что угодно об обучении, ценах, расписании или площадке.');
        }
        document.getElementById('chat-input').focus();
    } else {
        win.classList.add('hidden');
        btn.style.display = 'flex';
    }
}

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatBotMessage(value) {
    let html = escapeHtml(value);
    html = html.replace(/(https?:\/\/[^\s<]+)/g, (url) =>
        `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return html.replace(/\n/g, '<br>');
}

function addMessage(role, text) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    if (role === 'bot') {
        div.innerHTML = formatBotMessage(text);
    } else {
        div.textContent = text;
    }
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

async function sendMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    addMessage('user', text);
    chatHistory.push({ role: 'user', content: text });

    addMessage('bot', '⏳ Думаю...');
    const loadingMsg = document.getElementById('chat-messages').lastChild;

    try {
        const res = await fetch('/api/chat/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, history: chatHistory.slice(-10) }),
        });
        const data = await res.json();
        loadingMsg.remove();
        addMessage('bot', data.reply);
        chatHistory.push({ role: 'assistant', content: data.reply });
    } catch {
        loadingMsg.remove();
        addMessage('bot', 'Извините, произошла ошибка. Попробуйте позже или позвоните +7 702 718 22 33');
    }
}

// ===== THROTTLED SCROLL HANDLER =====
function throttledScroll(callback, limit) {
    let inThrottle = false;
    let lastRaf = null;
    
    return function() {
        if (inThrottle) return;
        inThrottle = true;
        
        if (lastRaf) cancelAnimationFrame(lastRaf);
        
        lastRaf = requestAnimationFrame(() => {
            callback();
            inThrottle = false;
        });
    };
}

// ===== HEADER SCROLL BEHAVIOR (throttled) =====
const header = document.getElementById('header');

window.addEventListener('scroll', throttledScroll(() => {
    const currentScroll = window.scrollY || window.pageYOffset;
    
    if (currentScroll > 100) {
        header.classList.add('scrolled');
    } else {
        header.classList.remove('scrolled');
    }
}, 100));

// ===== SCROLL PROGRESS BAR (throttled) =====
const progressBar = document.getElementById('scroll-progress');

window.addEventListener('scroll', throttledScroll(() => {
    const winScroll = document.documentElement.scrollTop || document.body.scrollTop;
    const height = document.documentElement.scrollHeight - document.documentElement.clientHeight;
    const scrolled = (winScroll / height) * 100;
    progressBar.style.width = scrolled + '%';
    progressBar.style.transform = 'translateZ(0)';
}, 100));

// ===== MOBILE MENU =====
// Всё оформление открытого меню задаётся классом .nav.open в CSS —
// инлайновые стили здесь не нужны и только мешают теме.
document.getElementById('mobile-toggle').addEventListener('click', function() {
    document.getElementById('nav').classList.toggle('open');
});

// ===== SMOOTH SCROLL FOR NAV LINKS =====
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        const href = this.getAttribute('href');
        if (href === '#' || !href.startsWith('#')) return;
        
        e.preventDefault();
        const target = document.querySelector(href);
        if (target) {
            // Закрываем мобильное меню перед переходом
            document.getElementById('nav').classList.remove('open');

            // Use native smooth scroll
            target.scrollIntoView({
                behavior: 'smooth',
                block: 'start'
            });
        }
    });
});

// ===== INITIALIZATION =====
document.addEventListener('DOMContentLoaded', async () => {
    // Initialize scroll observer
    new ScrollObserver();
    
    // Load stats (locations and instructors count)
    try {
        const res = await fetch('/api/stats');
        if (res.ok) {
            const stats = await res.json();
            
            // Количество инструкторов (первая карточка со счётчиком)
            const instructorsCounter = document.querySelector('.hero-stat .counter');
            const instructorsLabel = document.getElementById('instructors-label');
            if (instructorsCounter && stats.instructors_count) {
                instructorsCounter.dataset.target = stats.instructors_count;
                // Правильное склонение
                if (instructorsLabel) {
                    const count = stats.instructors_count;
                    let label = 'инструкторов';
                    if (count % 10 === 1 && count % 100 !== 11) {
                        label = 'инструктор';
                    } else if ([2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)) {
                        label = 'инструктора';
                    }
                    instructorsLabel.textContent = label;
                }
            }
        }
    } catch (e) {
        console.error('Failed to load stats:', e);
    }

    // Typewriter animation
    const typewriterEl = document.getElementById('typewriter');
    if (typewriterEl) {
        const words = ['уверенно', 'легко', 'быстро', 'безопасно', 'качественно'];
        let wordIndex = 0;
        let charIndex = 0;
        let isDeleting = false;
        let isPaused = false;

        function typeStep() {
            const currentWord = words[wordIndex];
            if (isPaused) {
                isPaused = false;
                isDeleting = true;
                setTimeout(typeStep, 50);
                return;
            }
            if (!isDeleting) {
                charIndex++;
                typewriterEl.textContent = currentWord.substring(0, charIndex);
                if (charIndex === currentWord.length) {
                    isPaused = true;
                    setTimeout(typeStep, 1500);
                    return;
                }
                setTimeout(typeStep, 100);
            } else {
                charIndex--;
                typewriterEl.textContent = currentWord.substring(0, charIndex);
                if (charIndex === 0) {
                    isDeleting = false;
                    wordIndex = (wordIndex + 1) % words.length;
                    setTimeout(typeStep, 300);
                    return;
                }
                setTimeout(typeStep, 50);
            }
        }
        setTimeout(typeStep, 1000);
    }
    
    // Initialize counters
    // Счётчик в hero виден сразу, поэтому запускаем его напрямую: так цифра
    // гарантированно доходит до значения из /api/stats, а не остаётся на
    // стартовом «3» из разметки.
    document.querySelectorAll('.counter').forEach(el => {
        const counter = new Counter(el);
        counter.start();
        // Страховка: если таймеры в фоне «придушатся», ставим точное значение.
        setTimeout(() => { el.textContent = counter.target + counter.suffix; }, counter.duration + 300);
    });

    // Initialize magnetic buttons (desktop only)
    if (window.innerWidth > 768) {
        document.querySelectorAll('.magnetic').forEach(btn => {
            new MagneticButton(btn);
        });
    }
    
    // Initialize tilt cards (desktop only)
    if (window.innerWidth > 768) {
        document.querySelectorAll('.tilt-card').forEach(card => {
            new TiltCard(card);
        });
    }
    
    // Load FAQ
    // Если API недоступен, показываем базовые вопросы — страница не должна
    // зависать на «Загрузка...».
    const FAQ_FALLBACK = [
        { question: 'Сколько стоит занятие?', answer: 'Одно практическое занятие длится 1 час и стоит 7 000 ₸. Других платежей нет.' },
        { question: 'Где проходят занятия?', answer: 'На площадке по адресу Циолковского 30 в Павлодаре.' },
        { question: 'Можно выбрать механику или автомат?', answer: 'Да. Автомобили есть и с МКПП, и с АКПП — выбираете при записи.' },
        { question: 'Как записаться?', answer: 'Через Telegram-бот или по телефону +7 702 718 22 33.' },
        { question: 'Сколько занятий нужно?', answer: 'Зависит от вашей подготовки: кто-то уверенно едет уже после нескольких занятий, кому-то нужно больше практики.' }
    ];

    function renderFaq(list, items) {
        list.innerHTML = items.map(f =>
            `<details class="faq-item">
                <summary>${f.question}</summary>
                <p>${f.answer}</p>
            </details>`
        ).join('');
    }

    const faqList = document.getElementById('faq-list');
    try {
        const res = await fetch('/api/faq');
        if (res.ok) {
            const items = await res.json();
            renderFaq(faqList, items && items.length ? items : FAQ_FALLBACK);
        } else {
            renderFaq(faqList, FAQ_FALLBACK);
        }
    } catch (e) {
        console.error('Failed to load FAQ:', e);
        renderFaq(faqList, FAQ_FALLBACK);
    }

    // Load instructors
    const INSTRUCTOR_FALLBACK = [
        { name: 'Алексей Иванов', transmission: 'Механика (МКПП)', experience_years: 8, description: 'Спокойный и опытный инструктор, обучает безопасному вождению в городских условиях.' },
        { name: 'Сергей Петров', transmission: 'Автомат (АКПП)', experience_years: 6, description: 'Специалист по подготовке к экзамену в СпецЦОНе, разбирает каждую ошибку.' },
        { name: 'Дмитрий Сидоров', transmission: 'МКПП / АКПП', experience_years: 10, description: 'Инструктор высшей категории, индивидуальная методика для любых уровней.' }
    ];

    function renderInstructors(instructors) {
        const container = document.getElementById('instructors-cards');
        if (!container) return;
        container.innerHTML = instructors.map(i =>
            `<div class="instructor-card tilt-card">
                <div class="card-shine"></div>
                <div class="instructor-avatar">👨‍🏫</div>
                <h3>${i.name}</h3>
                <span class="instructor-spec">${i.transmission || 'МКПП / АКПП'}</span>
                <p class="instructor-exp">⏱️ Стаж: ${i.experience_years || 0} лет</p>
                ${i.description ? `<p class="instructor-desc">${i.description}</p>` : ''}
            </div>`
        ).join('');

        container.querySelectorAll('.tilt-card').forEach(card => {
            if (typeof TiltCard !== 'undefined' && window.innerWidth > 768) new TiltCard(card);
        });
    }

    try {
        const res = await fetch('/api/instructors');
        let instructors = [];
        if (res.ok) instructors = await res.json();
        renderInstructors(instructors && instructors.length ? instructors : INSTRUCTOR_FALLBACK);
    } catch (e) {
        console.error('Failed to load instructors:', e);
        renderInstructors(INSTRUCTOR_FALLBACK);
    }
});
