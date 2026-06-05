/* ═══════════════════════════════════════════
   DABER Admin — Core JS (shared across all pages)
   ═══════════════════════════════════════════ */

// ── Constants ──
const POS_RU = {
  noun: 'сущ.', verb: 'глаг.', adj: 'прил.', adv: 'нареч.',
  prep: 'предл.', conj: 'союз', pron: 'мест.', num: 'числ.',
  intj: 'межд.', particle: 'част.', pref: 'прист.', suff: 'суф.',
  art: 'арт.', det: 'опред.', phrase: 'фраза'
};
const GENDER_RU = { '': '—', m: 'м.р.', f: 'ж.р.' };
const NUMBER_RU = { '': '—', s: 'ед.', p: 'мн.' };

function posLabel(w) {
  const p = POS_RU[w.pos_slug] || w.pos_slug || '?';
  if (w.pos_slug === 'phrase') return p;
  const parts = [p];
  if (w.gender && w.gender !== '') parts.push(GENDER_RU[w.gender] || w.gender);
  if (w.number && w.number !== '') parts.push(NUMBER_RU[w.number] || w.number);
  return parts.join(' · ');
}

// ── Auth ──
async function checkAuth() {
  try {
    const res = await fetch('/admin/api/check');
    if (!res.ok) throw new Error('no session');
    loadAllCounts();
  } catch(e) {
    window.location.href = '/admin/login';
  }
}

function logout() {
  document.cookie = 'daber_admin_session=; max-age=0; path=/';
  window.location.href = '/admin/login';
}

// ── Counters ──
async function loadAllCounts() {
  try {
    const [p, a, r, f, c, d] = await Promise.all([
      fetch('/admin/api/pending?status=pending').then(r => r.ok ? r.json() : []),
      fetch('/admin/api/pending?status=approved').then(r => r.ok ? r.json() : []),
      fetch('/admin/api/pending?status=rejected').then(r => r.ok ? r.json() : []),
      fetch('/admin/api/feedback').then(r => r.ok ? r.json() : []),
      fetch('/admin/api/contact').then(r => r.ok ? r.json() : []),
      fetch('/admin/api/duplicates').then(r => r.ok ? r.json() : [])
    ]);
    const setCnt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    setCnt('count-pending', p.length);
    setCnt('count-approved', a.length);
    setCnt('count-rejected', r.length);
    setCnt('count-feedback', f.length);
    setCnt('count-contact', c.filter(m => !m.resolved).length);
    setCnt('count-duplicates', d.length);
  } catch(e) { /* counters stay as-is */ }
}

// ── Sidebar mobile ──
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebarOverlay').classList.toggle('show');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebarOverlay').classList.remove('show');
}

function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// ── Bootstrap ──
async function initPage() {
  await checkAuth();
  if (typeof onPageLoad === 'function') onPageLoad();
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPage);
} else {
  initPage();
}
