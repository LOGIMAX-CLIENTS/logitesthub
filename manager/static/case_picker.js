// Test case picker (Test Run page, Create a Scheduled Run): folders as "Module › Sub-module" with their cases -
// web, Tester Assistant and Tester CLI ones alike - tick a whole folder or single cases.
// casePicker({ body, data, form, search, ready, source, count, folders, expand, clear, project })
//   data = {folders: [{id, name, project}], cases: [{id, tc, title, folder, project, ready, last, source, by, tasks, mine,
//           at, server}], selected: [ids], me}
//   With me: "My test cases" first (the user's own, newest work first, with PM task codes and whether they ran on a
//   server environment yet), then every folder of the project under "All test cases in <project>" (closed until opened).
//   form: on submit, one hidden "ids" input per ticked case is added.  Returns {setProject(id), selected()}.
window.casePicker = function (o) {
  var data = o.data, sel = new Set(data.selected || []), body = o.body;
  var q = '', readyOnly = false, src = '', project = o.project || null, open = new Set();
  var SRC = { assistant: 'Assistant', cli: 'CLI', manual: 'Manual' };
  var groups = [], byId = {}, mineList = [], allOpen = false;
  function build() {
    groups = data.folders.filter(function (f) { return !project || f.project === project; })
      .map(function (f) { return { id: f.id, name: f.name, cases: [] }; });
    byId = {}; groups.forEach(function (g) { byId[g.id] = g; });
    var none = { id: 0, name: '(no folder)', cases: [] };
    data.cases.forEach(function (c) { if (!project || c.project === project) (byId[c.folder] || none).cases.push(c); });
    if (none.cases.length) groups.push(none);
    groups.forEach(function (g) { if (g.cases.some(function (c) { return sel.has(c.id); })) open.add(g.id); });
    mineList = data.me ? data.cases.filter(function (c) { return c.mine && (!project || c.project === project); })
      .sort(function (a, b) { return (b.at || '').localeCompare(a.at || '') || b.tc - a.tc; }) : [];
    // a selected case outside "My test cases" opens the project's folders
    allOpen = !mineList.length || data.cases.some(function (c) { return sel.has(c.id) && !c.mine && (!project || c.project === project); });
  }
  var FOLDER = '<svg viewBox="0 0 24 24" width="16" height="16" class="cp-ficon"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l2 2h7.4A2.5 2.5 0 0 1 21 9.5v8A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5Z" fill="currentColor"/></svg>';
  var PERSON = '<svg viewBox="0 0 24 24" width="16" height="16" class="cp-ficon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>';
  var MINE_FIRST = 10;
  var CHEV ='<svg class="cp-chev" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="m9 6 6 6-6 6"/></svg>';
  function caseRow(c, mineRow) {
    var extra = '';
    if (mineRow) {
      (c.tasks || []).forEach(function (t) { extra += '<span class="cp-task" title="PM task">' + esc(t) + '</span>'; });
      extra += c.server
        ? '<span class="badge st-' + esc(c.server.status.toLowerCase()) + '" title="Latest run on ' + esc(c.server.env) + ', ' + esc(c.server.at) + '">' +
          esc(c.server.env) + ': ' + esc(c.server.status) + '</span>'
        : '<span class="cp-notlive" title="Not run on a server environment yet (only on a PC / localhost)">Not run on a server</span>';
    } else if (c.last) extra = '<span class="badge st-' + c.last.toLowerCase() + '">' + esc(c.last) + '</span>';
    return '<label class="cp-case"><input type="checkbox" class="form-check-input cp-ccb" value="' + c.id + '"' + (sel.has(c.id) ? ' checked' : '') + '>' +
      '<span class="cp-tc">TC-' + c.tc + '</span><span class="cp-title">' + esc(c.title) + '</span>' +
      '<span class="cp-src cp-src-' + esc(c.source) + '" title="Made with ' + esc(SRC[c.source] || c.source) + (c.by ? ' by ' + esc(c.by) : '') + '">' + esc(SRC[c.source] || c.source) + '</span>' +
      (c.ready ? '<span class="qa-tag">Ready for QA</span>' : '') + extra + '</label>';
  }
  // a folder row: key = folder id, 'mine' or 'all'; withBox = a tick box for the whole group
  function head(key, name, icon, n, total, isOpen, all, some, cls, withBox) {
    return '<div class="cp-folder' + (cls ? ' ' + cls : '') + '" role="treeitem" aria-expanded="' + isOpen + '">' +
      '<div class="cp-frow">' + (withBox === false ? '' : '<input type="checkbox" class="form-check-input cp-fcb" data-f="' + key + '"' + (all ? ' checked' : '') +
      (some && !all ? ' data-mixed="1"' : '') + ' aria-label="Select all in ' + esc(name) + '">') +
      '<button type="button" class="cp-open" data-f="' + key + '">' + CHEV + icon + '<span class="cp-fname">' + esc(name) + '</span></button>' +
      '<span class="cp-fcount' + (n ? ' on' : '') + '">' + n + ' / ' + total + '</span></div>';
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (ch) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]; }); }
  function visible(c) {
    if (readyOnly && !c.ready) return false;
    if (src && c.source !== src) return false;
    return !q || ('tc-' + c.tc).indexOf(q) !== -1 || c.title.toLowerCase().indexOf(q) !== -1;
  }
  function render() {
    var html = '', wrapped = false;
    if (mineList.length) {   // the user's own cases first, then the whole project under one folder
      var mv = mineList.filter(visible);
      if (mv.length) {
        var mOpen = !open.has('mine-closed');
        html += head('mine', 'My test cases', PERSON, mineList.filter(function (c) { return sel.has(c.id); }).length, mineList.length, mOpen,
                     mv.every(function (c) { return sel.has(c.id); }), mv.some(function (c) { return sel.has(c.id); }), 'cp-mine');
        if (mOpen) {   // the newest 10 (all while searching); the rest behind "Show all"
          var shown = q || src || open.has('mine-all') ? mv : mv.slice(0, MINE_FIRST);
          html += '<div class="cp-cases" role="group">' + shown.map(function (c) { return caseRow(c, true); }).join('') +
            (shown.length < mv.length ? '<button type="button" class="btn btn-link btn-sm cp-more" data-more="1">Show all ' + mv.length + '</button>' : '') + '</div>';
        }
        html += '</div>';
      }
      var total = groups.reduce(function (a, g) { return a + g.cases.length; }, 0);
      var pn = groups.reduce(function (a, g) { return a + g.cases.filter(function (c) { return sel.has(c.id); }).length; }, 0);
      var pOpen = allOpen || !!q || !!src;
      html += head('all', 'All test cases in ' + ((o.projectName && o.projectName()) || 'this project'), FOLDER, pn, total, pOpen, false, false, 'cp-all', false);
      if (!pOpen) { body.innerHTML = html + '</div>'; finish(); return; }
      html += '<div class="cp-all-body">';
      wrapped = true;
    }
    groups.forEach(function (g) {
      var vis = g.cases.filter(visible);
      if (!vis.length) return;   // only folders that have (matching) cases
      var n = g.cases.filter(function (c) { return sel.has(c.id); }).length, all = vis.every(function (c) { return sel.has(c.id); });
      var some = vis.some(function (c) { return sel.has(c.id); });
      var isOpen = open.has(g.id) || !!q || !!src;
      html += head(g.id, g.name, FOLDER, n, g.cases.length, isOpen, all, some);
      if (isOpen) {
        html += '<div class="cp-cases" role="group">';
        vis.forEach(function (c) { html += caseRow(c, false); });
        html += '</div>';
      }
      html += '</div>';
    });
    if (wrapped) html += '</div></div>';
    body.innerHTML = html || '<div class="cp-empty p-4">' + (q || readyOnly || src ? 'No test cases match.' : 'No test cases in this project yet.') + '</div>';
    finish();
  }
  function finish() {
    body.querySelectorAll('.cp-fcb[data-mixed]').forEach(function (x) { x.indeterminate = true; });
    count();
  }
  function count() {
    var fs = new Set(), n = 0;
    data.cases.forEach(function (c) { if (sel.has(c.id) && (!project || c.project === project)) { n++; fs.add(c.folder || 0); } });
    if (o.count) o.count.textContent = n;
    if (o.folders) o.folders.textContent = fs.size;
  }
  body.addEventListener('click', function (e) {
    if (e.target.closest('.cp-more')) { open.add('mine-all'); render(); return; }
    var b = e.target.closest('.cp-open');
    if (!b) return;
    if (b.dataset.f === 'mine') { open.has('mine-closed') ? open.delete('mine-closed') : open.add('mine-closed'); render(); return; }
    if (b.dataset.f === 'all') { allOpen = !allOpen; render(); return; }
    var f = +b.dataset.f; open.has(f) ? open.delete(f) : open.add(f); render();
  });
  body.addEventListener('change', function (e) {
    var t = e.target;
    if (t.classList.contains('cp-ccb')) { t.checked ? sel.add(+t.value) : sel.delete(+t.value); render(); }
    if (t.classList.contains('cp-fcb') && t.dataset.f === 'mine') {
      mineList.filter(visible).forEach(function (c) { t.checked ? sel.add(c.id) : sel.delete(c.id); });
      render(); return;
    }
    if (t.classList.contains('cp-fcb')) {
      var g = byId[+t.dataset.f] || groups.filter(function (x) { return x.id === +t.dataset.f; })[0];
      g.cases.filter(visible).forEach(function (c) { t.checked ? sel.add(c.id) : sel.delete(c.id); });
      render();
    }
  });
  if (o.search) o.search.addEventListener('input', function () { q = this.value.trim().toLowerCase(); render(); });
  if (o.ready) o.ready.addEventListener('change', function () { readyOnly = this.checked; render(); });
  if (o.source) o.source.addEventListener('change', function () { src = this.value; render(); });
  if (o.expand) o.expand.addEventListener('click', function () {
    var every = allOpen && groups.every(function (g) { return open.has(g.id); });
    groups.forEach(function (g) { every ? open.delete(g.id) : open.add(g.id); });
    if (!every) allOpen = true;
    this.textContent = every ? 'Expand all' : 'Collapse all'; render();
  });
  if (o.clear) o.clear.addEventListener('click', function () { sel.clear(); render(); });
  if (o.form) o.form.addEventListener('submit', function (e) {
    var ids = data.cases.filter(function (c) { return sel.has(c.id) && (!project || c.project === project); });
    if (o.requireOne && !ids.length) { e.preventDefault(); alert('Select at least one test case.'); return; }
    if (!o.requireOne && !ids.length && !confirm('Save with no test cases selected?')) { e.preventDefault(); return; }
    var f = this; f.querySelectorAll('input[name=ids]').forEach(function (x) { x.remove(); });
    ids.forEach(function (c) { var i = document.createElement('input'); i.type = 'hidden'; i.name = 'ids'; i.value = c.id; f.appendChild(i); });
  });
  build(); render();
  return {
    setProject: function (p) { project = p ? +p : null; open.clear(); build(); render(); },
    selected: function () { return Array.from(sel); },
  };
};
