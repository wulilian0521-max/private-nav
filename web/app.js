'use strict';
const $ = s => document.querySelector(s);
let state = {bookmarks: [], devices: [], categories: [], currentDevice: ''};
let category = '全部网址';
let toastTimer;
let searchTimer, searchController, searchVersion = 0, searchResult = null;
function toast(message) {
  $('#toast').textContent = message; $('#toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 5000);
}
async function api(path, body, signal) {
  const response = await fetch(path, body === undefined ? {signal} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal});
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) { showLogin(); }
    throw new Error(data.error || '操作失败，请重试。');
  }
  return data;
}
function showLogin() {
  clearAiSearch();
  document.querySelectorAll('dialog[open]').forEach(d => d.close());
  $('#dashboard').hidden = true; $('#login').hidden = false;
  state = {bookmarks: [], devices: [], categories: [], currentDevice: ''};
  $('#cards').replaceChildren(); $('#device-list').replaceChildren();
  $('#pair-code').value = ''; $('#pair-result').hidden = true;
}
async function load() {
  if (location.protocol === 'file:') {
    showLogin();
    $('#login-form').hidden = true;
    $('#login .muted').textContent = '请通过部署后的网站地址打开导航页。';
    document.querySelectorAll('#login .helper').forEach(p => { p.hidden = true; });
    const help = document.createElement('p'); help.className = 'helper'; help.textContent = '这是网页版的页面文件，需要与网站服务一起部署。部署后，你可以直接用电脑或 iPhone 的浏览器访问，无需安装客户端。';
    $('#login-form').after(help);
    return;
  }
  try {
    state = await api('/api/state');
    $('#login').hidden = true; $('#dashboard').hidden = false;
    render();
  } catch (error) {
    if (!$('#login').hidden) return;
    showLogin();
    toast('暂时无法连接导航页，请检查网络或稍后重试。');
  }
}
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function allCategories() { return [...new Set([...state.categories, ...state.bookmarks.map(b => b.category)])]; }
function render() {
  const categories = ['全部网址', '置顶', ...allCategories()];
  if (!categories.includes(category)) category = '全部网址';
  const nav = $('#categories'); nav.replaceChildren();
  for (const [index, name] of categories.entries()) {
    const count = state.bookmarks.filter(b => name === '全部网址' || (name === '置顶' ? b.pinned : b.category === name)).length;
    const button = el('button', name === category ? 'active' : '');
    button.type = 'button'; button.setAttribute('aria-current', name === category ? 'page' : 'false');
    button.append(el('span', 'nav-symbol', ['▦', '☆', '▤', '⌘', '›_', '◇', '⋯'][index] || '◇'), el('span', '', name), el('span', 'nav-count', String(count)));
    button.addEventListener('click', () => { clearAiSearch(); category = name; render(); }); nav.append(button);
  }
  const options = $('#category-options'); options.replaceChildren();
  for (const name of allCategories()) { const option = el('option'); option.value = name; options.append(option); }
  renderCards();
  updateSearchButton();
}
function renderCards() {
  const query = $('#search').value.trim().toLocaleLowerCase();
  const ai = searchResult?.query === $('#search').value.trim() ? searchResult : null;
  const items = ai ? ai.ids.map(id => state.bookmarks.find(b => b.id === id)).filter(Boolean) : keywordMatches(query);
  $('#collection-title').textContent = ai ? 'AI 查找' : category;
  $('#count').textContent = `${items.length} 个入口`;
  const cards = $('#cards'); cards.replaceChildren();
  for (const [index, item] of items.entries()) {
    const featured = !!item.pinned && index < 2 && !query && (category === '全部网址' || category === '置顶');
    const card = el('article', featured ? `card card-featured${index === 1 ? ' feature-dark' : ''}` : 'card');
    const top = el('div', 'card-top'); top.append(el('span', 'site-icon', [...item.title][0].toLocaleUpperCase()), el('span', 'site-category', item.category));
    if (item.pinned) top.append(el('span', 'pin-mark', '☆ 已置顶'));
    const link = el('a', 'card-link'); link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.append(el('h3', '', item.title)); link.setAttribute('aria-label', `打开 ${item.title}（新标签页）`);
    const parsed = new URL(item.url); const host = parsed.host + (parsed.pathname === '/' ? '' : parsed.pathname);
    const url = el('p', 'site-url', host); url.title = item.url;
    const bottom = el('div', 'card-bottom'); bottom.append(el('span', 'open-label', '打开入口 ↗'));
    const actions = el('div', 'card-actions');
    const edit = el('button', '', '编辑'); edit.setAttribute('aria-label', `编辑 ${item.title}`); edit.addEventListener('click', () => openEditor(item));
    const remove = el('button', '', '删除'); remove.setAttribute('aria-label', `删除 ${item.title}`); remove.addEventListener('click', async () => {
      if (!window.confirm(`从导航页删除「${item.title}」？`)) return;
      try { await api('/api/bookmarks/delete', {id: item.id}); await load(); toast('已删除网址'); } catch (e) { toast(e.message); }
    });
    actions.append(edit, remove); bottom.append(actions); card.append(top, link, url);
    if (item.note) card.append(el('p', 'site-note', item.note));
    card.append(bottom); cards.append(card);
  }
  $('#empty').hidden = items.length !== 0;
  $('#empty-title').textContent = ai ? '没有找到符合描述的入口' : query ? '没有找到这个入口' : state.bookmarks.length ? '这里还没有网址' : '给常用入口一个家';
  $('#empty-description').textContent = ai ? '换个说法描述用途，或者试试网站名称。' : query ? '试试其他关键词，或者添加一个新网址。' : '先添加一个服务器控制台、API 管理页或 CLI 工具页面。';
  $('#empty-add').textContent = state.bookmarks.length ? '＋ 添加网址' : '＋ 添加第一个网址';
}
function keywordMatches(query) {
  return state.bookmarks.filter(b => (category === '全部网址' || (category === '置顶' ? b.pinned : b.category === category)) && `${b.title} ${b.url} ${b.note} ${b.category}`.toLocaleLowerCase().includes(query));
}
function searchStatus(message = '', status = '') {
  $('#search-status').textContent = message;
  $('#search-status').hidden = !message;
  $('#search-status').dataset.status = status;
}
function updateSearchButton() {
  $('#ai-search-button').disabled = !!searchController || !state.aiNamingEnabled || !$('#search').value.trim();
  $('#ai-search-button').textContent = searchController ? '查找中…' : 'AI 查找';
  $('#search-form').setAttribute('aria-busy', String(!!searchController));
}
function clearAiSearch() {
  clearTimeout(searchTimer); searchController?.abort(); searchController = null;
  searchVersion++; searchResult = null; searchStatus(); updateSearchButton();
}
function onSearchInput(event) {
  clearAiSearch(); renderCards();
  const query = $('#search').value.trim();
  if (event?.isComposing || !query || !state.aiNamingEnabled) return;
  if (query.length >= 2 && keywordMatches(query.toLocaleLowerCase()).length === 0) {
    searchStatus('可以描述用途，AI 会在你的收藏中查找');
    searchTimer = setTimeout(() => runAiSearch(), 1100);
  }
}
async function runAiSearch() {
  clearTimeout(searchTimer);
  const query = $('#search').value.trim();
  if (!query || searchController || !state.aiNamingEnabled || $('#dashboard').hidden) return;
  clearAiSearch();
  const version = searchVersion;
  searchController = new AbortController();
  category = '全部网址'; render();
  searchStatus('正在从你的收藏中查找…', 'busy'); updateSearchButton();
  try {
    const result = await api('/api/bookmarks/search', {query}, searchController.signal);
    if (version !== searchVersion || query !== $('#search').value.trim()) return;
    if (result.ai_status !== 'ready' || !Array.isArray(result.ids)) throw new Error('unavailable');
    searchResult = {query, ids: result.ids.filter(id => state.bookmarks.some(b => b.id === id))};
    renderCards();
    searchStatus(searchResult.ids.length ? `AI 找到 ${searchResult.ids.length} 个相关入口` : '没有找到符合描述的收藏，换个说法试试');
  } catch (error) {
    if (version !== searchVersion || error.name === 'AbortError') return;
    searchStatus('AI 暂时无法查找，仍可使用名称、网址或备注搜索', 'error');
  } finally {
    if (version === searchVersion) { searchController = null; updateSearchButton(); }
  }
}
function openEditor(item) {
  const form = $('#bookmark-form'); cancelIdentification(); form.reset(); generatedFields = {}; lastIdentifiedUrl = '';
  for (const name of ['id', 'title', 'url', 'category', 'note']) form.elements[name].value = item ? item[name] : '';
  form.elements.pinned.checked = !!item?.pinned;
  $('#edit-title').textContent = item ? '编辑网址' : '添加网址';
  $('#identify-button').textContent = state.aiNamingEnabled ? 'AI 识别' : '识别网址';
  identifyStatus(item ? '可手动修改信息，也可以重新识别网址' : state.aiNamingEnabled ? '粘贴网址后，AI 自动填写名称、分类和中文简介' : '粘贴网址后，自动填写名称和分类');
  $('#edit-dialog').showModal();
}
let identifyTimer, identifyController, identifyVersion = 0, lastIdentifiedUrl = '', identifyingUrl = '';
let generatedFields = {};
function identifyStatus(message, status = '') {
  $('#identify-status').textContent = message;
  $('#identify-status').dataset.status = status;
}
function identifyBusy(busy) {
  $('#identify-button').disabled = busy;
  $('#bookmark-form [type="submit"]').disabled = busy;
  $('#bookmark-form').setAttribute('aria-busy', String(busy));
}
function cancelIdentification() {
  clearTimeout(identifyTimer); identifyController?.abort(); identifyController = null;
  identifyingUrl = '';
  identifyVersion++; identifyBusy(false);
}
function candidateUrl(value) {
  value = value.trim();
  if (!value) return '';
  if (!value.includes('://')) value = 'https://' + value;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || /\s/.test(value)) return '';
    if (!url.hostname.includes('.') && url.hostname !== 'localhost' && !url.hostname.includes(':')) return '';
    return value;
  } catch { return ''; }
}
async function identifyUrl(force = false) {
  clearTimeout(identifyTimer);
  const form = $('#bookmark-form');
  if (!$('#edit-dialog').open || (form.elements.id.value && !force)) return;
  const url = candidateUrl(form.elements.url.value);
  if (!url) {
    if (force) identifyStatus('请先填写有效的网址', 'error');
    return;
  }
  if (url === identifyingUrl || (!force && url === lastIdentifiedUrl)) return;
  cancelIdentification();
  const version = identifyVersion;
  const before = {title: form.elements.title.value, category: form.elements.category.value, note: form.elements.note.value};
  form.elements.url.value = url;
  identifyController = new AbortController(); identifyBusy(true);
  identifyingUrl = url;
  identifyStatus(state.aiNamingEnabled ? '正在请 AI 推荐名称、分类和中文简介…' : '正在读取网站信息…', 'busy');
  try {
    const result = await api('/api/bookmarks/describe', {url}, identifyController.signal);
    if (version !== identifyVersion || !$('#edit-dialog').open || candidateUrl(form.elements.url.value) !== url) return;
    for (const field of ['title', 'category', 'note']) {
      if (typeof result[field] === 'string' && ((force && field !== 'note') || !before[field] || before[field] === generatedFields[field]) && form.elements[field].value === before[field]) {
        form.elements[field].value = result[field]; generatedFields[field] = result[field];
      }
    }
    lastIdentifiedUrl = url;
    identifyStatus(result.source === 'ai' ? 'AI 推荐已填写，可修改后保存' : result.ai_status === 'unavailable' ? 'AI 暂时不可用，已根据网站信息填写，可修改后保存' : '已根据网站信息填写，可修改后保存');
  } catch (error) {
    if (version !== identifyVersion || error.name === 'AbortError') return;
    identifyStatus('暂时无法自动识别，可重试或手动填写名称和分类', 'error');
  } finally {
    if (version === identifyVersion) { identifyController = null; identifyingUrl = ''; identifyBusy(false); }
  }
}
$('#bookmark-form [name="url"]').addEventListener('input', () => {
  cancelIdentification(); lastIdentifiedUrl = '';
  const form = $('#bookmark-form');
  if (form.elements.id.value) return;
  for (const field of ['title', 'category', 'note']) {
    if (generatedFields[field] && form.elements[field].value === generatedFields[field]) form.elements[field].value = '';
  }
  generatedFields = {};
  identifyStatus(state.aiNamingEnabled ? '粘贴网址后，AI 自动填写名称、分类和中文简介' : '粘贴网址后，自动填写名称和分类');
  identifyTimer = setTimeout(() => identifyUrl(), 800);
});
$('#bookmark-form [name="url"]').addEventListener('blur', () => identifyUrl());
$('#bookmark-form [name="url"]').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); identifyUrl(true); }
});
$('#identify-button').addEventListener('click', () => identifyUrl(true));
$('#edit-dialog').addEventListener('close', cancelIdentification);

$('#login-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; const button = form.querySelector('button'); button.disabled = true;
  try { await api('/api/pair', {name: form.elements.name.value, code: form.elements.code.value}); form.reset(); await load(); toast('此浏览器已获得授权'); } catch (e) { toast(e.message); } finally { button.disabled = false; }
});
$('#bookmark-form').addEventListener('submit', async event => {
  event.preventDefault(); if (identifyController) return;
  const form = event.currentTarget; const button = form.querySelector('[type="submit"]'); button.disabled = true;
  const body = Object.fromEntries(new FormData(form)); body.pinned = form.elements.pinned.checked;
  try { await api('/api/bookmarks/save', body); $('#edit-dialog').close(); await load(); toast('网址已保存'); } catch (e) { toast(e.message); } finally { button.disabled = false; }
});
$('#add-button').addEventListener('click', () => openEditor()); $('#empty-add').addEventListener('click', () => openEditor());
$('#search').addEventListener('input', onSearchInput);
$('#search').addEventListener('compositionstart', clearAiSearch);
$('#search').addEventListener('compositionend', onSearchInput);
$('#search-form').addEventListener('submit', event => { event.preventDefault(); runAiSearch(); });
$('#workspace-link').addEventListener('click', event => {
  event.preventDefault();
  clearAiSearch(); category = '全部网址'; $('#search').value = ''; render();
  $('#all-sites').scrollIntoView({block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  $('#collection-title').focus({preventScroll: true});
});
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
document.addEventListener('keydown', event => { if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName) && !document.querySelector('dialog[open]') && !$('#dashboard').hidden) { event.preventDefault(); $('#search').focus(); } });
function renderDevices() {
  const list = $('#device-list'); list.replaceChildren();
  for (const device of state.devices) {
    const row = el('div', 'device-row'); const info = el('div');
    info.append(el('strong', '', device.name), el('small', '', '最近使用：' + new Date(device.last_seen * 1000).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})));
    row.append(info);
    if (device.id === state.currentDevice) row.append(el('span', 'current', '当前浏览器'));
    else {
      const button = el('button', 'text-button', '移除'); button.addEventListener('click', async () => {
        if (!window.confirm(`撤销「${device.name}」的访问权限？之后需要重新配对。`)) return;
        try { await api('/api/devices/remove', {id: device.id}); await load(); renderDevices(); $('#pair-result').hidden = true; $('#pair-code').value = ''; toast('已撤销访问权限'); } catch(e) { toast(e.message); }
      }); row.append(button);
    }
    list.append(row);
  }
  $('#pair-button').disabled = state.devices.length >= 2;
  $('#pair-button').textContent = state.devices.length >= 2 ? '已授权两台浏览器' : '生成另一台设备的配对码';
}
$('#devices-button').addEventListener('click', async () => { await load(); if ($('#dashboard').hidden) return; renderDevices(); $('#pair-result').hidden = true; $('#pair-code').value = ''; $('#devices-dialog').showModal(); });
$('#devices-dialog').addEventListener('close', () => { $('#pair-code').value = ''; $('#pair-result').hidden = true; });
$('#pair-button').addEventListener('click', async () => {
  try { const result = await api('/api/pair-code', {}); $('#pair-code').value = result.code; $('#pair-result').hidden = false; } catch(e) { toast(e.message); }
});
$('#copy-code').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('#pair-code').value); toast('配对码已复制'); } catch { $('#pair-code').focus(); $('#pair-code').select(); toast('请长按或按 Command+C 复制配对码'); }
});
$('#export-button').addEventListener('click', () => {
  const data = {version: 1, bookmarks: state.bookmarks.map(({title,url,category,note,pinned}) => ({title,url,category,note,pinned:!!pinned}))};
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type:'application/json'}));
  const link = el('a'); link.href = url; link.download = '私人导航备份-' + new Date().toLocaleDateString('sv-SE') + '.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('备份包含网址和备注，请存放在自己的设备中');
});
$('#import-button').addEventListener('click', () => $('#import-file').click());
$('#import-file').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 1024 * 1024) throw new Error('备份文件不能大于 1 MB。');
    const data = JSON.parse(await file.text());
    if (!data || data.version !== 1 || !Array.isArray(data.bookmarks)) throw new Error('请使用本导航页导出的 JSON 备份。');
    const result = await api('/api/import', {bookmarks: data.bookmarks}); await load(); toast(`已导入 ${result.added} 个网址，重复网址自动跳过`);
  } catch(e) { toast(e instanceof SyntaxError ? '备份文件格式不正确。' : e.message); } finally { event.target.value = ''; }
});
window.addEventListener('focus', () => { if (!$('#dashboard').hidden && !document.querySelector('dialog[open]')) load(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('#dashboard').hidden && !document.querySelector('dialog[open]')) load(); });
load();
