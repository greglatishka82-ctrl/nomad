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

// ===== SUBMIT ORDER =====
async function submitOrder() {
    const btn = document.getElementById('order-submit-btn');
    const name = document.getElementById('order-name').value.trim();
    const phone = document.getElementById('order-phone').value.trim();
    const email = document.getElementById('order-email').value.trim();

    if (!name) {
        document.getElementById('order-name').style.borderColor = '#ef4444';
        return;
    }
    if (!phone) {
        document.getElementById('order-phone').style.borderColor = '#ef4444';
        return;
    }
    if (!email) {
        document.getElementById('order-email').style.borderColor = '#ef4444';
        return;
    }

    document.getElementById('order-name').style.borderColor = '';
    document.getElementById('order-phone').style.borderColor = '';
    document.getElementById('order-email').style.borderColor = '';

    btn.disabled = true;
    btn.textContent = 'Отправка...';

    try {
        const res = await fetch('/api/orders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                phone,
                email,
                payment_method: 'cash',
            }),
        });
        const data = await res.json();
        if (res.ok) {
            document.getElementById('order-form').innerHTML = `
                <div class="payment-result">
                    <h4>✓ Заявка принята!</h4>
                    <p>${data.message || 'Мы свяжемся с вами в ближайшее время'}</p>
                    <p style="margin-top:16px">Оплата производится наличными в автошколе</p>
                </div>
            `;
        } else {
            document.getElementById('order-result').style.display = 'block';
            document.getElementById('order-result').innerHTML = `
                <p style="color:#ef4444;text-align:center">${data.error || 'Ошибка при отправке заявки'}</p>
            `;
        }
    } catch {
        document.getElementById('order-result').style.display = 'block';
        document.getElementById('order-result').innerHTML = `
            <p style="color:#ef4444;text-align:center">Ошибка сети. Попробуйте позже.</p>
        `;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Отправить заявку';
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
            addMessage('bot', 'Здравствуйте! Я помощник автошколы NOMAD. Задайте любой вопрос об обучении, ценах, расписании или площадках.');
        }
        document.getElementById('chat-input').focus();
    } else {
        win.classList.add('hidden');
        btn.style.display = 'flex';
    }
}

function addMessage(role, text) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = `msg ${role}`;
    div.textContent = text;
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
document.getElementById('mobile-toggle').addEventListener('click', function() {
    const nav = document.getElementById('nav');
    nav.classList.toggle('open');
    
    if (nav.classList.contains('open')) {
        nav.style.display = 'flex';
        nav.style.flexDirection = 'column';
        nav.style.position = 'absolute';
        nav.style.top = '72px';
        nav.style.left = '0';
        nav.style.right = '0';
        nav.style.background = 'rgba(10, 14, 39, 0.98)';
        nav.style.backdropFilter = 'blur(20px)';
        nav.style.padding = '24px';
        nav.style.borderBottom = '1px solid var(--border)';
    } else {
        nav.style.display = '';
        nav.style.flexDirection = '';
        nav.style.position = '';
        nav.style.top = '';
        nav.style.left = '';
        nav.style.right = '';
        nav.style.background = '';
        nav.style.backdropFilter = '';
        nav.style.padding = '';
        nav.style.borderBottom = '';
    }
});

// ===== SMOOTH SCROLL FOR NAV LINKS =====
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        const href = this.getAttribute('href');
        if (href === '#' || !href.startsWith('#')) return;
        
        e.preventDefault();
        const target = document.querySelector(href);
        if (target) {
            // Close mobile menu first
            const nav = document.getElementById('nav');
            if (nav.classList.contains('open')) {
                nav.classList.remove('open');
                nav.style.display = '';
                nav.style.flexDirection = '';
                nav.style.position = '';
                nav.style.top = '';
                nav.style.left = '';
                nav.style.right = '';
                nav.style.background = '';
                nav.style.backdropFilter = '';
                nav.style.padding = '';
                nav.style.borderBottom = '';
            }
            
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
    const counters = document.querySelectorAll('.counter');
    const counterObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const counter = new Counter(entry.target);
                counter.start();
                counterObserver.unobserve(entry.target);
            }
        });
    }, { threshold: 0.5 });
    
    counters.forEach(counter => counterObserver.observe(counter));
    
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
    try {
        const res = await fetch('/api/faq');
        if (res.ok) {
            const items = await res.json();
            const list = document.getElementById('faq-list');
            if (items.length) {
                list.innerHTML = items.map(f =>
                    `<details class="faq-item">
                        <summary>${f.question}</summary>
                        <p>${f.answer}</p>
                    </details>`
                ).join('');
            } else {
                list.innerHTML = '<p style="color:var(--text-secondary);text-align:center">Вопросы скоро появятся</p>';
            }
        }
    } catch (e) {
        console.error('Failed to load FAQ:', e);
    }

    // Load instructors
    try {
        const res = await fetch('/api/instructors');
        if (res.ok) {
            const instructors = await res.json();
            const container = document.getElementById('instructors-cards');
            if (container && instructors.length) {
                container.innerHTML = instructors.map(i =>
                    `<div class="instructor-card tilt-card">
                        <div class="card-shine"></div>
                        <div class="instructor-avatar">${i.name.charAt(0)}</div>
                        <h3>${i.name}</h3>
                        <p class="instructor-spec">${i.transmission}</p>
                        <p class="instructor-exp">Стаж: ${i.experience_years} лет</p>
                        ${i.description ? `<p class="instructor-desc">${i.description}</p>` : ''}
                    </div>`
                ).join('');
                
                container.querySelectorAll('.tilt-card').forEach(card => {
                    new TiltCard(card);
                });
            } else if (container) {
                container.innerHTML = '<p style="color:var(--text-secondary);text-align:center;grid-column:1/-1">Информация об инструкторах обновляется</p>';
            }
        }
    } catch (e) {
        console.error('Failed to load instructors:', e);
    }
});
