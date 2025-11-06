const API_BASE = '';
function apiUrl(path){ const base = API_BASE || window.location.origin; return base.replace(/\/$/, '') + path; }
async function apiGet(path){ const res = await fetch(apiUrl(path)); if(!res.ok) throw new Error('API '+res.status); return res.json(); }
async function load(){
  try{
    document.getElementById('trend-text').innerText = '이번 주 흐름 자동 생성 중...';
    const res = await apiGet('/api/news?limit=10');
    const list = document.getElementById('list'); list.innerHTML='';
    res.items.forEach(it=>{
      const el = document.createElement('div'); el.className='card';
      el.innerHTML = `<h3><a href="/articles/${it.id}.html">${escapeHtml(it.title)}</a></h3><p>${escapeHtml(it.summary||'')}</p><div class="meta"><span>${escapeHtml(it.source||'')}</span><span>신뢰:${(it.confidence||0).toFixed(2)}</span></div>`;
      list.appendChild(el);
    });
    if(res.items.length===0) list.innerHTML='<div style="padding:20px;color:#6b7280">최근 기사 없음</div>';
  }catch(e){ console.error(e); document.getElementById('list').innerText = '데이터를 불러올 수 없습니다.' }
}
function escapeHtml(s){ if(!s) return ''; return s.replace(/[&<>"']/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }
document.getElementById('refresh').addEventListener('click', ()=> load());
window.addEventListener('load', ()=> load());