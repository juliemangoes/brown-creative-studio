/* On-set mode: tick off shots, add notes, flag retakes, capture new ideas, with or without signal.
   Every change is applied on screen at once, kept in localStorage, and queued; the queue is sent
   to the server when online (and again after a reload). The server applies each change idempotently. */
(function () {
  var root = document.getElementById('onset');
  if (!root) return;
  var initial = JSON.parse(document.getElementById('onset-data').textContent);
  var key = 'onset:' + initial.shoot;
  var csrf = root.getAttribute('data-csrf');

  function load() {
    try { return JSON.parse(localStorage.getItem(key)) || null; } catch (e) { return null; }
  }
  function save() {
    try { localStorage.setItem(key, JSON.stringify(state)); } catch (e) { /* storage full or blocked: changes stay in memory */ }
  }
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) { var r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16); });
  }

  var saved = load();
  var state = { shots: initial.shots, ops: [] };
  if (saved && saved.ops && saved.ops.length) {
    // Unsent changes from an earlier visit win over the page's copy.
    state.ops = saved.ops;
    var byId = {};
    (saved.shots || []).forEach(function (s) { byId[s.id] = s; });
    state.shots = initial.shots.map(function (s) { return byId[s.id] || s; });
    (saved.shots || []).forEach(function (s) { if (!initial.shots.some(function (i) { return i.id === s.id; })) state.shots.push(s); });
  }

  var list = root.querySelector('[data-list]');
  var status = root.querySelector('[data-status]');
  var counter = root.querySelector('[data-counter]');

  function labelFor(s) { return { planned: 'To shoot', captured: 'Captured', retake: 'Retake', skipped: 'Skipped' }[s] || s; }

  function render() {
    list.innerHTML = '';
    var done = 0;
    state.shots.forEach(function (shot) {
      if (shot.status === 'captured') done++;
      var li = document.createElement('li');
      li.className = 'panel panel-body space-y-3';
      li.setAttribute('data-state', shot.status);
      var head = document.createElement('div');
      head.className = 'flex items-start justify-between gap-3';
      var text = document.createElement('div');
      text.innerHTML = '<p class="meta"></p><p class="text-16 font-medium"></p><p class="meta"></p>';
      text.children[0].textContent = (shot.code || 'New') + (shot.priority === 'must' ? ' · must have' : '') + (shot.location ? ' · ' + shot.location : '');
      text.children[1].textContent = shot.description;
      text.children[2].textContent = shot.post ? 'For: ' + shot.post : (shot.unplanned ? 'Captured on set' : '');
      var badge = document.createElement('span');
      badge.className = 'status ' + ({ captured: 'status-success', retake: 'status-warning', skipped: 'status-neutral' }[shot.status] || 'status-info');
      badge.textContent = labelFor(shot.status);
      head.appendChild(text);
      head.appendChild(badge);
      li.appendChild(head);
      var actions = document.createElement('div');
      actions.className = 'flex flex-wrap gap-2';
      [['captured', 'Got it', 'btn-primary'], ['retake', 'Retake', 'btn-secondary'], ['skipped', 'Skip', 'btn-quiet'], ['planned', 'Undo', 'btn-quiet']].forEach(function (a) {
        if (a[0] === shot.status) return;
        if (a[0] === 'planned' && shot.status === 'planned') return;
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn ' + a[2];
        b.textContent = a[1];
        b.setAttribute('aria-label', a[1] + ': ' + shot.description);
        b.addEventListener('click', function () { change(shot, { op: 'status', status: a[0] }); });
        actions.appendChild(b);
      });
      li.appendChild(actions);
      var note = document.createElement('textarea');
      note.className = 'textarea';
      note.rows = 2;
      note.placeholder = 'Notes (lens, take number, what to fix)';
      note.setAttribute('aria-label', 'Notes for ' + shot.description);
      note.value = shot.notes || '';
      note.addEventListener('change', function () { change(shot, { op: 'note', text: note.value }); });
      li.appendChild(note);
      list.appendChild(li);
    });
    counter.textContent = done + ' of ' + state.shots.length + ' captured';
    var pending = state.ops.length;
    status.textContent = navigator.onLine ? (pending ? 'Saving ' + pending + ' change' + (pending === 1 ? '' : 's') + '…' : 'All changes saved') : (pending ? 'Offline: ' + pending + ' change' + (pending === 1 ? '' : 's') + ' saved on this phone' : 'Offline');
    status.className = 'text-14 ' + (navigator.onLine ? (pending ? 'text-ink-muted' : 'text-success') : 'text-warning');
  }

  function change(shot, op) {
    op.shot = shot.id;
    op.at = new Date().toISOString();
    if (op.op === 'status') shot.status = op.status;
    if (op.op === 'note') shot.notes = op.text;
    state.ops.push(op);
    save();
    render();
    flush();
  }

  root.querySelector('[data-idea-form]').addEventListener('submit', function (event) {
    event.preventDefault();
    var input = this.querySelector('input');
    var textValue = input.value.trim();
    if (!textValue) return;
    var id = uuid();
    state.shots.push({ id: id, code: '', description: textValue, status: 'captured', notes: '', priority: 'nice', location: '', unplanned: true, post: '' });
    state.ops.push({ op: 'idea', id: id, text: textValue, status: 'captured', at: new Date().toISOString() });
    input.value = '';
    save();
    render();
    flush();
  });

  var sending = false;
  function flush() {
    if (sending || !navigator.onLine || !state.ops.length) return;
    sending = true;
    var batch = state.ops.slice();
    fetch(initial.sync, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({ ops: batch }),
    }).then(function (response) {
      if (response.status === 403 || response.redirected) throw new Error('signed-out');
      if (!response.ok) throw new Error('server');
      return response.json();
    }).then(function () {
      state.ops = state.ops.slice(batch.length);
      save();
    }).catch(function (err) {
      if (err && err.message === 'signed-out') status.textContent = 'Sign in again to save your changes (they are kept on this phone).';
    }).finally(function () {
      sending = false;
      render();
      if (state.ops.length && navigator.onLine) setTimeout(flush, 5000);
    });
  }

  window.addEventListener('online', function () { render(); flush(); });
  window.addEventListener('offline', render);
  render();
  flush();

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register(root.getAttribute('data-sw'), { scope: '/shoots/' }).catch(function () {});
  }
  // Keep this page and its files for the next visit without signal.
  if (window.caches) {
    var urls = [location.href];
    document.querySelectorAll('link[rel=stylesheet], script[src]').forEach(function (el) { urls.push(el.href || el.src); });
    caches.open('onset-v1').then(function (cache) { return cache.addAll(urls); }).catch(function () {});
  }
})();
