'use strict';
const $ = (selector) => document.querySelector(selector);
let items = [];
let filter = 'open';
const labels = { inbox: 'Inbox', next: 'Up next', active: 'In progress', done: 'Done' };

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Family-Desk': '1' },
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== '/api/login') showLogin();
    throw new Error(result.error || 'Something went wrong. Please try again.');
  }
  return result;
}

function showLogin() {
  items = [];
  $('#tasks').replaceChildren();
  $('#notes').replaceChildren();
  $('#project-records').replaceChildren();
  $('#project-jobs').replaceChildren();
  $('#password-form').reset();
  $('#password-editor').close();
  $('#item-form').reset();
  $('#editor').close();
  $('#desk').hidden = true;
  $('#logout').hidden = true;
  $('#account').hidden = true;
  $('#login-view').hidden = false;
  $('#password').value = '';
}

async function load() {
  const result = await api('/api/items');
  items = result.items;
  $('#login-view').hidden = true;
  $('#desk').hidden = false;
  $('#logout').hidden = false;
  $('#account').hidden = false;
  $('#notice').textContent = '';
  render();
  try { await loadProject(); }
  catch (error) { $('#notice').textContent = `Requests loaded. Project progress is unavailable: ${error.message}`; }
}

async function loadProject() {
  const project = await api('/api/project');
  $('#project-records').replaceChildren();
  $('#project-jobs').replaceChildren();
  for (const record of project.records) {
    const card = element('article', 'note');
    card.append(element('p', 'eyebrow', record.kind), element('h3', '', record.key.replaceAll('_', ' ')), element('p', 'body-text', record.content));
    card.append(element('p', 'owner', `${record.actor} · ${new Date(record.updated_at * 1000).toLocaleDateString()} · revision ${record.revision}`));
    $('#project-records').append(card);
  }
  for (const job of project.jobs) {
    const card = element('article', 'card');
    card.append(element('span', 'badge', job.lease_expired ? 'Needs review · ownership expired' : job.state), element('h3', '', job.title));
    if (job.result) card.append(element('p', 'body-text', job.result));
    if (job.evidence) card.append(element('p', 'body-text', job.evidence));
    $('#project-jobs').append(card);
  }
  if (!project.records.length && !project.jobs.length) $('#project-records').append(element('p', 'empty', 'Project decisions and completed work will appear here.'));
}

function element(tag, className, content) {
  const el = document.createElement(tag);
  el.className = className;
  if (content !== undefined) el.textContent = content;
  return el;
}

function render() {
  const tasks = items.filter((item) => item.kind === 'task');
  $('#inbox-count').textContent = tasks.filter((item) => item.status === 'inbox').length;
  $('#active-count').textContent = tasks.filter((item) => item.status === 'active').length;
  $('#done-count').textContent = tasks.filter((item) => item.status === 'done').length;
  const visible = tasks.filter((item) => filter === 'all' || (filter === 'done' ? item.status === 'done' : item.status !== 'done'));
  $('#tasks').replaceChildren();
  for (const item of visible) {
    const card = element('article', 'card');
    const meta = element('div', 'card-meta');
    meta.append(element('span', `badge ${item.status}`, labels[item.status]));
    const edit = element('button', 'quiet edit-button', 'Edit ↗');
    edit.setAttribute('aria-label', `Edit ${item.title}`);
    edit.addEventListener('click', () => openEditor('task', item));
    meta.append(edit);
    card.append(meta, element('h3', '', item.title));
    if (item.body) card.append(element('p', 'body-text', item.body));
    card.append(element('p', 'owner', item.owner ? `For ${item.owner}` : 'For our family'));
    $('#tasks').append(card);
  }
  if (!visible.length) $('#tasks').append(element('div', 'empty', filter === 'done' ? 'Your little wins will appear here. Every step counts.' : 'A clear desk. Add a wish, a chore, or something you’d like help with.'));
  $('#notes').replaceChildren();
  const notes = items.filter((item) => item.kind === 'note');
  for (const item of notes) {
    const card = element('article', 'note');
    card.append(element('h3', '', item.title));
    if (item.body) card.append(element('p', 'body-text', item.body));
    if (item.owner) card.append(element('p', 'owner', item.owner));
    const edit = element('button', 'quiet', 'Edit note ↗');
    edit.setAttribute('aria-label', `Edit note ${item.title}`);
    edit.addEventListener('click', () => openEditor('note', item));
    card.append(edit);
    $('#notes').append(card);
  }
  if (!notes.length) $('#notes').append(element('div', 'empty note-empty', 'A place for the things you don’t want to forget.'));
}

function openEditor(kind, item = null) {
  const form = $('#item-form');
  form.reset();
  form.elements.kind.value = kind;
  form.elements.id.value = item?.id || '';
  for (const key of ['title', 'body', 'owner', 'status']) {
    if (item) form.elements[key].value = item[key];
  }
  $('#editor-title').textContent = `${item ? 'Edit' : 'Add'} ${kind === 'note' ? 'a note' : 'a request'}`;
  $('#status-field').hidden = kind === 'note';
  $('#editor-error').textContent = '';
  $('#editor').showModal();
  $('#item-title').focus();
}

$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  button.disabled = true;
  $('#login-error').textContent = '';
  try {
    await api('/api/login', { password: $('#password').value });
    $('#password').value = '';
    await load();
  } catch (error) { $('#login-error').textContent = error.message; }
  finally { button.disabled = false; }
});
$('#logout').addEventListener('click', async () => {
  try { await api('/api/logout', {}); showLogin(); }
  catch (error) { $('#notice').textContent = error.message; }
});
$('#new-task').addEventListener('click', () => openEditor('task'));
$('#new-note').addEventListener('click', () => openEditor('note'));
$('#refresh-project').addEventListener('click', async () => {
  try { await loadProject(); $('#notice').textContent = ''; }
  catch (error) { $('#notice').textContent = error.message; }
});
$('#account').addEventListener('click', () => {
  $('#password-form').reset();
  $('#password-error').textContent = '';
  $('#password-editor').showModal();
});
$('#cancel-password').addEventListener('click', () => $('#password-editor').close());
$('#password-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if ($('#new-password').value !== $('#repeat-password').value) {
    $('#password-error').textContent = 'The passwords did not match.';
    return;
  }
  const button = event.currentTarget.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    await api('/api/password', { password: $('#new-password').value });
    event.target.reset();
    $('#password-editor').close();
  } catch (error) { $('#password-error').textContent = error.message; }
  finally { button.disabled = false; }
});
for (const id of ['#close-editor', '#cancel-editor']) $(id).addEventListener('click', () => $('#editor').close());
document.querySelectorAll('[data-filter]').forEach((button) => button.addEventListener('click', () => {
  filter = button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach((tab) => tab.setAttribute('aria-pressed', String(tab === button)));
  render();
}));
$('#item-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('[type="submit"]');
  button.disabled = true;
  const data = Object.fromEntries(new FormData(event.currentTarget));
  if (data.id) data.id = Number(data.id); else delete data.id;
  try {
    await api('/api/items', data);
    $('#editor').close();
    try { await load(); }
    catch (error) { $('#notice').textContent = `Saved, but could not refresh the desk. ${error.message}`; }
  } catch (error) { $('#editor-error').textContent = error.message; }
  finally { button.disabled = false; }
});
$('#today').textContent = new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric' }).format(new Date()).toUpperCase();
async function start() {
  const token = new URLSearchParams(location.hash.slice(1)).get('access');
  if (token) history.replaceState(null, '', location.pathname);
  try {
    if (token) { await api('/api/access', { token }); await load(); }
    else if ((await api('/api/session')).authenticated) await load();
  }
  catch (error) { $('#login-error').textContent = `Could not open the desk. ${error.message}`; }
}
start();
