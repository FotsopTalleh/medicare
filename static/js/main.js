/**
 * SaveTheMommy MediCare Dashboard — main.js
 * Handles sidebar toggle, toast notifications, and general interactions
 */

// ─── Sidebar toggle ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {

    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar       = document.getElementById('sidebar');

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', function () {
            sidebar.classList.toggle('active');
        });
    }

    // Close sidebar when clicking outside on mobile
    document.addEventListener('click', function (event) {
        if (window.innerWidth <= 768 &&
            sidebar && sidebar.classList.contains('active') &&
            !sidebar.contains(event.target) &&
            event.target !== sidebarToggle) {
            sidebar.classList.remove('active');
        }
    });

    // ── Bootstrap tooltips ──────────────────────────────────────────────────
    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (el) {
        new bootstrap.Tooltip(el);
    });

    // ── Auto-dismiss flash alerts after 5 s ────────────────────────────────
    document.querySelectorAll('.alert:not(.alert-permanent)').forEach(function (alert) {
        setTimeout(function () {
            try { new bootstrap.Alert(alert).close(); } catch (_) {}
        }, 5000);
    });

    // ── Mark active sidebar link ────────────────────────────────────────────
    const currentPath = window.location.pathname;
    document.querySelectorAll('.sidebar .nav-link').forEach(function (link) {
        const href = link.getAttribute('href');
        if (href && href !== '#' && currentPath === href) {
            link.classList.add('active');
        }
    });

    // ── Stagger pop-in for unread message items ─────────────────────────────
    document.querySelectorAll('.message-item.unread').forEach(function (item, i) {
        item.style.animationDelay = (i * 90) + 'ms';
        item.classList.add('pop-in');
    });

    // ── Poll for new referrals every 30 s (badge update) ───────────────────
    startUnreadPolling();
});

// ─── Toast Notification System ────────────────────────────────────────────────

/**
 * Show a referral notification toast.
 * @param {string} title   - Bold title text
 * @param {string} message - Body HTML string
 * @param {string} link    - URL to navigate to on click
 * @param {number} duration - Auto-close ms (0 = manual only)
 */
function showReferralToast(title, message, link, duration = 7000) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = 'referral-toast';
    toast.innerHTML = `
        <div class="toast-icon"><i class="fas fa-envelope"></i></div>
        <div class="toast-body">
            <div class="toast-title">📩 ${title}</div>
            <div class="toast-text">${message}</div>
            <a href="${link}" class="toast-link">View in Messages →</a>
        </div>
        <button class="toast-close" onclick="closeToast(this.closest('.referral-toast'))" aria-label="Close">
            <i class="fas fa-times"></i>
        </button>
    `;

    container.appendChild(toast);

    if (duration > 0) {
        setTimeout(function () { closeToast(toast); }, duration);
    }

    return toast;  // caller can add extra classes (e.g. new-msg)
}

/**
 * Animate-out and remove a toast element.
 * @param {HTMLElement} toastEl
 */
function closeToast(toastEl) {
    if (!toastEl || toastEl.classList.contains('hiding')) return;
    toastEl.classList.add('hiding');
    setTimeout(function () {
        if (toastEl.parentNode) toastEl.parentNode.removeChild(toastEl);
    }, 350);
}

// ─── Unread badge polling (every 30 s) ───────────────────────────────────────
let _lastKnownUnread = null;

function startUnreadPolling() {
    // Only poll when logged in (badge element exists somewhere in DOM)
    setInterval(function () {
        fetch('/api/unread-count')
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) return;
                const count = data.unread_count || 0;

                // Update badge on nav link
                const badge = document.getElementById('unread-badge');
                if (badge) {
                    badge.textContent = count;
                    badge.style.display = count > 0 ? '' : 'none';
                }

                // Show toast only when new messages arrive during this session
                if (_lastKnownUnread !== null && count > _lastKnownUnread) {
                    const diff = count - _lastKnownUnread;
                    const toastEl = showReferralToast(
                        'New Referral' + (diff > 1 ? 's' : ''),
                        `You received <strong>${diff}</strong> new referral message${diff > 1 ? 's' : ''}.`,
                        '/messages'
                    );
                    // Apply the enhanced bounce+pulse style
                    if (toastEl) toastEl.classList.add('new-msg');
                    // Also show the top-of-page banner
                    showNewMsgBanner(
                        `<i class="fas fa-envelope me-2"></i>${diff} new referral message${diff > 1 ? 's' : ''} received`,
                        '/messages'
                    );
                }

                _lastKnownUnread = count;
            })
            .catch(function () { /* silently ignore network errors */ });
    }, 30000); // every 30 seconds
}

// ─── New-message top banner ───────────────────────────────────────────────────
/**
 * Drop a slide-down banner from the top of the page.
 * @param {string} html    - Banner inner HTML
 * @param {string} link    - URL to navigate to on click
 * @param {number} duration - Auto-hide after ms
 */
function showNewMsgBanner(html, link, duration = 5000) {
    let banner = document.querySelector('.new-msg-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.className = 'new-msg-banner';
        document.body.appendChild(banner);
    }
    banner.innerHTML = html;
    banner.onclick = function () {
        window.location.href = link;
    };
    // Trigger show
    requestAnimationFrame(function () {
        banner.classList.add('show');
    });
    // Auto-hide
    setTimeout(function () {
        banner.classList.remove('show');
    }, duration);
}

// ─── Sent animation helper ────────────────────────────────────────────────────
/**
 * Show the paper-plane sent animation, then call onComplete.
 * @param {Function} onComplete - Called after the animation finishes
 */
function playSentAnimation(onComplete) {
    // Build overlay if not already present
    let overlay = document.getElementById('send-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'send-overlay';
        overlay.className = 'send-overlay';
        overlay.innerHTML = `
            <div class="plane-wrap">
                <i class="plane-icon fas fa-paper-plane"></i>
                <div class="check-ring"></div>
                <div class="sent-label">Referral Sent!</div>
                <div class="sent-sub">The patient has been referred successfully.</div>
            </div>
        `;
        document.body.appendChild(overlay);
    }

    // Reset animations by cloning
    const fresh = overlay.cloneNode(true);
    fresh.id = 'send-overlay';
    overlay.parentNode.replaceChild(fresh, overlay);
    overlay = fresh;

    // Activate
    requestAnimationFrame(function () { overlay.classList.add('active'); });

    // After animation completes, call onComplete
    const delay = 1800;
    setTimeout(function () {
        overlay.classList.remove('active');
        setTimeout(function () {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            if (typeof onComplete === 'function') onComplete();
        }, 280);
    }, delay);
}

// ─── Form helpers ─────────────────────────────────────────────────────────────
function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return true;
    form.classList.add('was-validated');
    return form.checkValidity();
}

// Expose globals
window.showReferralToast  = showReferralToast;
window.closeToast         = closeToast;
window.showNewMsgBanner   = showNewMsgBanner;
window.playSentAnimation  = playSentAnimation;
window.MedicalDashboard   = { validateForm };