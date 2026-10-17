// Test case picker (Test Run page, Create a Scheduled Run): folders as "Module › Sub-module" with their cases -
// web, Tester Assistant and Tester CLI ones alike - tick a whole folder or single cases.
// casePicker({ body, data, form, search, ready, source, count, folders, expand, clear, project })
//   data = {folders: [{id, name, project}], cases: [{id, tc, title, folder, project, ready, last, source, by}], selected: [ids]}
//   form: on submit, one hidden "ids" input per ticked case is added.  Returns {setProject(id), selected()}.
window.casePicker = function (o) {
  var data = o.data, sel = new Set(data.selected || []), body = o.body;
  var q = '', readyOnly = false, src = '', project = o.project || null, open = new Set();
  var SRC = { assistant: 'Assistant', cli: 'CLI', manual: 'Manual' };
  var groups = [], byId = {};
  function build() {
    groups = data.folders.filter(function (f) { return !project || f.project === project; })
      .map(function (f) { return { id: f.id, name: f.name, cases: [] }; });
    byId = {}; groups.forEach(function (g) { byId[g.id] = g; });
    var none = { id: 0, name: '(no folder)', cases: [] };
    data.cases.forEach(function (c) { if (!project || c.project === project) (byId[c.folder] || none).cases.push(c); });
    if (none.cases.length) groups.push(none);
    groups.forEach(function (g) { if (g.cases.some(function (c) { return sel.has(c.id); })) open.add(g.id); });
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (ch) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]; }); }
  function visible(c) {
    if (readyOnly && !c.ready) return false;
    if (src && c.source !== src) return false;
    return !q || ('tc-' + c.tc).indexOf(q) !== -1 || c.title.toLowerCase().indexOf(q) !== -1;
  }
  function render() {
    var html = '';
    groups.forEach(function (g) {
      var vis = g.cases.filter(visible);
      if (!vis.length) return;   // only folders that have (matching) cases
      var n = g.cases.filter(function (c) { return sel.has(c.id); }).length, all = vis.every(function (c) { return sel.has(c.id); });
      var some = vis.some(function (c) { return sel.has(c.id); });
      var isOpen = open.has(g.id) || !!q || !!src;
      html += '<div class="cp-folder" role="treeitem" aria-expanded="' + isOpen + '">' +
        '<div class="cp-frow"><input type="checkbox" class="form-check-input cp-fcb" data-f="' + g.id + '"' + (all ? ' checked' : '') +
        (some && !all ? ' data-mixed="1"' : '') + ' aria-label="Select all in ' + esc(g.name) + '">' +
        '<button type="button" class="cp-open" data-f="' + g.id + '"><svg class="cp-chev" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="m9 6 6 6-6 6"/></svg>' +
        '<svg viewBox="0 0 24 24" width="16" height="16" class="cp-ficon"><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l2 2h7.4A2.5 2.5 0 0 1 21 9.5v8A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5Z" fill="currentColor"/></svg>' +
        '<span class="cp-fname">' + esc(g.name) + '</span></button>' +
        '<span class="cp-fcount' + (n ? ' on' : '') + '">' + n + ' / ' + g.cases.length + '</span></div>';
      if (isOpen) {
        html += '<div class="cp-cases" role="group">';
        vis.forEach(function (c) {
          html += '<label class="cp-case"><input type="checkbox" class="form-check-input cp-ccb" value="' + c.id + '"' + (sel.has(c.id) ? ' checked' : '') + '>' +
            '<span class="cp-tc">TC-' + c.tc + '</span><span class="cp-title">' + esc(c.title) + '</span>' +
            '<span class="cp-src cp-src-' + esc(c.source) + '" title="Made with ' + esc(SRC[c.source] || c.source) + (c.by ? ' by ' + esc(c.by) : '') + '">' + esc(SRC[c.source] || c.source) + '</span>' +
            (c.ready ? '<span class="qa-tag">Ready for QA</span>' : '') +
            (c.last ? '<span class="badge st-' + c.last.toLowerCase() + '">' + esc(c.last) + '</span>' : '') + '</label>';
        });
        html += '</div>';
      }
      html += '</div>';
    });
    body.innerHTML = html || '<div class="cp-empty p-4">' + (q || readyOnly || src ? 'No test cases match.' : 'No test cases in this project yet.') + '</div>';
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
    var b = e.target.closest('.cp-open');
    if (b) { var f = +b.dataset.f; open.has(f) ? open.delete(f) : open.add(f); render(); }
  });
  body.addEventListener('change', function (e) {
    var t = e.target;
    if (t.classList.contains('cp-ccb')) { t.checked ? sel.add(+t.value) : sel.delete(+t.value); render(); }
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
    var allOpen = groups.every(function (g) { return open.has(g.id); });
    groups.forEach(function (g) { allOpen ? open.delete(g.id) : open.add(g.id); });
    this.textContent = allOpen ? 'Expand all' : 'Collapse all'; render();
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
