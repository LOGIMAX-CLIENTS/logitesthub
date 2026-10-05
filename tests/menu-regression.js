#!/usr/bin/env node
// Regression checks for the menu reader (lib/menu.js): sample apps for every supported menu style and login
// situation, served from memory on a local port, each with the result it must give.
//   node tests/menu-regression.js            all samples
//   node tests/menu-regression.js --only pm  samples whose name contains "pm"
//   node tests/menu-regression.js --serve 5099   just serve the sample apps (UI testing)
// Exit code 0 = all passed. Live checks against real environments: python manager/manage.py check-menus
const http = require('http');
const path = require('path');
const { chromium } = require(path.join(__dirname, '..', 'node_modules', 'playwright'));
const { readMenu } = require('../lib/menu');

const ETAIL_LOGIN = path.join(__dirname, 'menu-fixtures-etail-login.md');   // written below: same steps as eTail's helper
const page = (title, body, script = '') => `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title>
<style>.collapse:not(.show),.dropdown-menu{display:none}.hide{display:none}</style></head><body>${body}<script>${script}</script></body></html>`;
const LOGIN_FORM = (extra = '') => `<form id="f" onsubmit="return go(event)">${extra}
  <input placeholder="Enter your email or username" type="text" id="u"><input type="password" id="p" placeholder="Enter your password">
  <button type="submit">Sign in</button></form><div id="msg"></div>`;

const FILES = {
  // ---- eTail (AdminLTE sidebar) + its own login page
  'etail-login.html': page('Logimax | Login', `<form onsubmit="return go(event)">
    <input placeholder="User Name" id="user"><input type="password" placeholder="Password" id="pass">
    <label for="br">Select Branch</label><select id="br"><option value="">--</option><option>All</option><option>HO</option></select>
    <button type="submit">Sign In</button></form>`,
    `function go(e){e.preventDefault(); if(user.value==='tester'&&pass.value==='secret123'&&br.value==='All') location.href='adminlte.html'; return false;}`),
  'adminlte.html': page('Dashboard | Logimax', `<aside class="main-sidebar"><ul class="sidebar-menu">
    <li class="header">MAIN NAVIGATION</li>
    <li><a href="dashboard"><i class="fa fa-home"></i> <span>Dashboard</span></a></li>
    <li class="treeview"><a href="#"><i class="fa"></i> <span>Retail Catalog</span><i class="fa pull-right"></i></a>
      <ul class="treeview-menu"><li><a href="metal"><i class="fa fa-circle-o"></i> Metal</a></li><li><a href="category"><i class="fa fa-circle-o"></i> Category</a></li><li><a href="product"><i class="fa fa-circle-o"></i> Product</a></li></ul></li>
    <li class="treeview"><a href="#"><span>Inventory</span><span class="label badge">New</span></a>
      <ul class="treeview-menu"><li><a href="lot_inward">Lot Inward</a></li><li><a href="lot_split">Lot Split</a></li></ul></li>
    <li class="treeview"><a href="#"><span>Masters</span></a><ul class="treeview-menu">
      <li class="treeview"><a href="#">Scheme</a><ul class="treeview-menu"><li><a href="scheme_group">Scheme Group</a></li></ul></li>
      <li><a href="customer">Customer</a></li></ul></li>
    <li class="treeview"><a href="#"><span>Settings</span></a><ul class="treeview-menu"><li><a href="profile">Profile</a></li><li><a href="notification">Notification</a></li></ul></li>
    <li><a href="logout"><span>Logout</span></a></li></ul></aside><section>Dashboard</section>`),

  // ---- PM-style React app: login form + icon-only sidebar that needs "Toggle Sidebar"
  'pm-login.html': page('Project Hub', LOGIN_FORM(),
    `function go(e){e.preventDefault(); if(u.value==='tester'&&p.value==='secret123') setTimeout(()=>location.href='pm-app.html',300); else msg.innerHTML='<div role=alert>Invalid username or password</div>'; return false;}`),
  'pm-app.html': page('Project Hub', `<header><button aria-label="Toggle Sidebar" id="t">≡</button></header>
    <aside id="side"><a href="/my-tasks"><svg></svg></a><a href="/feeds"><svg></svg></a></aside><main>All Tasks</main>`,
    `t.onclick=()=>{side.innerHTML='<a href="/my-tasks"><div><h1>Project Hub</h1><p>Project Management Platform</p></div></a><div><input><div>🔍</div></div>'+
      '<nav><ul><li><a href="/feeds"><span></span><span>Feed</span></a></li>'+
      '<li><button><span></span><span>Project Management</span></button><div><ul><li><a href="/my-tasks">My Tasks</a></li><li><a href="/tasks">All Tasks</a></li>'+
      '<li><a href="/iterations">Iterations<span class="badge">New</span></a></li></ul></div></li>'+
      '<li><button><span></span><span>Attendance</span></button><div><ul><li><a href="/approvals">Approvals</a></li><li><a href="/daily">Daily Register</a></li></ul></div></li></ul></nav>';};`),

  // ---- Bootstrap top navbar with dropdowns (items in the page, hidden)
  'bootstrap-topnav.html': page('Shop Admin', `<nav class="navbar"><ul class="navbar-nav">
    <li class="nav-item"><a class="nav-link" href="/home">Home</a></li>
    <li class="nav-item dropdown"><a class="nav-link dropdown-toggle" href="#" data-bs-toggle="dropdown">Sales</a>
      <div class="dropdown-menu"><a class="dropdown-item" href="/orders">Orders</a><a class="dropdown-item" href="/invoices">Invoices</a><a class="dropdown-item" href="/returns">Returns</a></div></li>
    <li class="nav-item dropdown"><a class="nav-link dropdown-toggle" href="#" data-bs-toggle="dropdown">Reports</a>
      <div class="dropdown-menu"><a class="dropdown-item" href="/daily">Daily</a><a class="dropdown-item" href="/monthly">Monthly</a></div></li></ul></nav>`),

  // ---- groups as headings followed by links (no nesting)
  'headings.html': page('Ops', `<aside><div class="brand">Acme</div>
    <h6>Operations</h6><a href="/dispatch">Dispatch</a><a href="/receiving">Receiving</a><a href="/stock">Stock</a>
    <h6>Finance</h6><a href="/ledger">Ledger</a><a href="/payments">Payments</a>
    <p class="caption">Admin</p><a href="/users">Users</a></aside>`),

  // ---- ARIA tree (Angular / Material tree navigation)
  'aria-tree.html': page('Tree', `<nav><ul role="tree">
    <li role="treeitem" aria-expanded="true"><span>Purchasing</span><ul role="group"><li role="treeitem"><a href="/po">Purchase Orders</a></li><li role="treeitem"><a href="/grn">GRN</a></li></ul></li>
    <li role="treeitem" aria-expanded="true"><span>Warehouse</span><ul role="group"><li role="treeitem"><a href="/bins">Bins</a></li></ul></li></ul></nav>`),

  // ---- Angular Material expansion panels
  'material.html': page('Material', `<mat-sidenav><mat-expansion-panel class="mat-expansion-panel">
    <mat-expansion-panel-header class="mat-expansion-panel-header"><span class="mat-content">Masters</span></mat-expansion-panel-header>
    <div class="mat-expansion-panel-content"><a mat-list-item href="/items">Items</a><a mat-list-item href="/units">Units</a></div></mat-expansion-panel>
    <mat-expansion-panel class="mat-expansion-panel"><mat-expansion-panel-header class="mat-expansion-panel-header"><span class="mat-content">Transactions</span></mat-expansion-panel-header>
    <div class="mat-expansion-panel-content"><a mat-list-item href="/sales">Sales</a><a mat-list-item href="/purchase">Purchase</a><a mat-list-item href="/transfer">Transfer</a></div></mat-expansion-panel></mat-sidenav>`),

  // ---- menus that exist only after a click (React / Headless UI / Radix)
  'click-dropdown.html': page('SaaS', `<header><a href="/home">Home</a>
    <button aria-haspopup="menu" data-m="products">Products</button><button aria-haspopup="menu" data-m="customers">Customers</button></header><div id="pop"></div>`,
    `const M={products:['Catalog','Pricing','Bundles'],customers:['Accounts','Segments']};
     document.querySelectorAll('[aria-haspopup]').forEach(b=>b.onclick=()=>{pop.innerHTML='<div role="menu">'+M[b.dataset.m].map(x=>'<a role="menuitem" href="/'+x.toLowerCase()+'">'+x+'</a>').join('')+'</div>';});
     document.addEventListener('keydown',e=>{if(e.key==='Escape')pop.innerHTML='';});`),

  // ---- details / summary and Bootstrap collapse sidebars
  'details.html': page('Docs', `<nav><details><summary>Setup</summary><a href="/install">Install</a><a href="/config">Configure</a></details>
    <details><summary>Usage</summary><a href="/run">Run</a></details></nav>`),
  'bootstrap-collapse.html': page('Clinic', `<div class="sidebar"><a data-bs-toggle="collapse" href="#c1">Billing</a>
    <div class="collapse" id="c1"><ul><li><a href="/bill">New Bill</a></li><li><a href="/bills">Bill List</a></li></ul></div>
    <a data-bs-toggle="collapse" href="#c2">Patients</a><div class="collapse" id="c2"><ul><li><a href="/reg">Registration</a></li></ul></div></div>`),

  // ---- StarGold-style collapsed sidebar: group buttons with only a tooltip; sub-pages are added on click (accordion)
  'collapsed-groups.html': page('Start Gold Admin', `<div class="admin-layout sidebar-is-collapsed"><aside class="sidebar sidebar-collapsed">
    <div class="sidebar-header"><div class="sidebar-logo"><img alt="startGOLD"></div><button class="sidebar-toggle"></button></div>
    <nav class="sidebar-nav"><a class="sidebar-link" href="/dashboard" title="Dashboard"><svg></svg></a>
      <div class="sidebar-group" data-g="Masters"><button class="sidebar-link sidebar-group-toggle" title="Masters"><svg></svg></button></div>
      <a class="sidebar-link" href="/premium" title="Rate Panel"><svg></svg></a>
      <div class="sidebar-group" data-g="Customers"><button class="sidebar-link sidebar-group-toggle" title="Customers"><svg></svg></button></div>
      <div class="sidebar-group" data-g="Settings"><button class="sidebar-link sidebar-group-toggle" title="Settings"><svg></svg></button></div></nav></aside>
    <header><h1>Admin Panel</h1><button>Logout</button></header><main>Dashboard</main></div>`,
    `const G={Masters:['Commodities','Vendors','Bank Master'],Customers:['Customer List','KYC Verification'],Settings:['Countries','Taxes','Configuration']};
     document.querySelectorAll('.sidebar-group-toggle').forEach(b=>b.onclick=()=>{
       document.querySelectorAll('.sidebar-sub').forEach(x=>x.remove());   // accordion: one group open at a time
       const g=b.parentElement.dataset.g, d=document.createElement('div'); d.className='sidebar-sub';
       d.innerHTML=G[g].map(x=>'<a href="/'+x.toLowerCase().replace(/ /g,'-')+'">'+x+'</a>').join(''); b.parentElement.appendChild(d);});`),

  // ---- login situations
  'login-wrong.html': page('Sign in', LOGIN_FORM(), `function go(e){e.preventDefault(); msg.innerHTML='<div class="alert alert-danger">Invalid username or password</div>'; return false;}`),
  'login-otp.html': page('Sign in', LOGIN_FORM(), `function go(e){e.preventDefault(); f.outerHTML='<p>Enter the OTP sent to your mobile</p><input name="otp" placeholder="OTP">'; return false;}`),
  'login-captcha.html': page('Sign in', LOGIN_FORM('<div class="g-recaptcha"></div>'), `function go(e){e.preventDefault(); return false;}`),
  'login-twostep.html': page('Sign in to your account', `<div id="s1"><input type="email" id="em" placeholder="Email"><button onclick="next()">Next</button></div><div id="s2"></div>`,
    `function next(){ if(em.value!=='tester') return; s1.innerHTML=''; s2.innerHTML='<input type="password" id="pw"><button onclick="if(pw.value===\\'secret123\\')location.href=\\'headings.html\\'">Sign in</button>'; }`),
  'login-company.html': page('Sign in', `<form onsubmit="return go(event)"><label for="co">Company Code</label><input id="co" type="text">
    <label for="ui">User Id</label><input id="ui" type="text"><label for="pw">Password</label><input id="pw" type="password">
    <button type="submit">Sign In</button></form><div id="msg"></div>`,
    `function go(e){e.preventDefault(); if(co.value==='ACME'&&ui.value==='tester'&&pw.value==='secret123') location.href='bootstrap-topnav.html'; else msg.innerHTML='<div role="alert">Unknown company or user</div>'; return false;}`),
  'home-nomenu.html': page('Welcome', `<main><h1>Welcome</h1><p>Pick an app from the launcher.</p></main>`),
};

const CASES = [   // name, base page, extra options, expected { ok, modules: {name: [subs...]}, reason regex, strategy }
  ['etail: eTail login steps + AdminLTE sidebar', 'etail-login.html', { etailLogin: true, branch: 'All' },
    { ok: true, login: 'etail', modules: { 'Retail Catalog': ['Metal', 'Category', 'Product'], Inventory: ['Lot Inward', 'Lot Split'], Masters: ['Scheme › Scheme Group', 'Customer'],
      Settings: ['Profile', 'Notification'] }, absent: ['Logout', 'MAIN NAVIGATION'] }],
  ['pm: generic login + icon-only React sidebar', 'pm-login.html', { etailLogin: true },
    { ok: true, login: 'generic', strategy: 'nested-list', modules: { 'Project Management': ['My Tasks', 'All Tasks', 'Iterations'], Attendance: ['Approvals', 'Daily Register'] },
      absent: ['Project Hub', 'Project HubProject Management Platform', '🔍'] }],
  ['bootstrap top navbar dropdowns', 'bootstrap-topnav.html', {}, { ok: true, modules: { Sales: ['Orders', 'Invoices', 'Returns'], Reports: ['Daily', 'Monthly'] } }],
  ['heading groups', 'headings.html', {}, { ok: true, modules: { Operations: ['Dispatch', 'Receiving', 'Stock'], Finance: ['Ledger', 'Payments'], Admin: ['Users'] } }],
  ['aria tree', 'aria-tree.html', {}, { ok: true, modules: { Purchasing: ['Purchase Orders', 'GRN'], Warehouse: ['Bins'] } }],
  ['material expansion panels', 'material.html', {}, { ok: true, modules: { Masters: ['Items', 'Units'], Transactions: ['Sales', 'Purchase', 'Transfer'] } }],
  ['click-only dropdowns', 'click-dropdown.html', {}, { ok: true, strategy: 'click-dropdowns', modules: { Products: ['Catalog', 'Pricing', 'Bundles'], Customers: ['Accounts', 'Segments'] } }],
  ['collapsed sidebar, groups filled on click (StarGold)', 'collapsed-groups.html', {},
    { ok: true, strategy: 'expand-groups', modules: { Masters: ['Commodities', 'Vendors', 'Bank Master'], Customers: ['Customer List', 'KYC Verification'],
      Settings: ['Countries', 'Taxes', 'Configuration'] }, absent: ['startGOLD', 'Logout'] }],
  ['details / summary', 'details.html', {}, { ok: true, modules: { Setup: ['Install', 'Configure'], Usage: ['Run'] } }],
  ['bootstrap collapse sidebar', 'bootstrap-collapse.html', {}, { ok: true, modules: { Billing: ['New Bill', 'Bill List'], Patients: ['Registration'] } }],
  ['login: two-step email then password', 'login-twostep.html', {}, { ok: true, modules: { Operations: ['Dispatch'] } }],
  ['login: wrong password -> app message', 'login-wrong.html', {}, { ok: false, stage: 'login', reason: /refused the login: "Invalid username or password"/ }],
  ['login: OTP -> clear reason', 'login-otp.html', {}, { ok: false, stage: 'login', reason: /OTP/ }],
  ['login: CAPTCHA -> clear reason', 'login-captcha.html', {}, { ok: false, stage: 'login', reason: /CAPTCHA/ }],
  ['login: company code, generic fails clearly', 'login-company.html', {}, { ok: false, stage: 'login', reason: /Unknown company or user/ }],
  ['login: company code with custom Login steps', 'login-company.html', {
    loginSteps: "Type 'ACME' into the Company Code field.\nType {{username}} into the User Id field.\nType {{password}} into the Password field.\nClick the Sign In button." },
    { ok: true, login: 'custom', modules: { Sales: ['Orders'] } }],
  ['login: custom step that is not standard -> reason', 'login-company.html', { loginSteps: 'Do the login dance.' }, { ok: false, stage: 'login', reason: /Login step 1 is not a standard step/ }],
  ['no menu on landing page -> reason', 'home-nomenu.html', {}, { ok: false, stage: 'menu', reason: /no menu was found.*Menu page URL/ }],
  ['Menu page URL opens the right page', 'home-nomenu.html', { menuUrl: 'adminlte.html' }, { ok: true, modules: { 'Retail Catalog': ['Category'] } }],
];

function check(res, exp) {
  const errs = [];
  if (res.ok !== exp.ok) errs.push(`ok=${res.ok} (expected ${exp.ok}) ${res.reason || ''}`);
  if (exp.stage && res.stage !== exp.stage) errs.push(`stage=${res.stage} (expected ${exp.stage})`);
  if (exp.reason && !exp.reason.test(res.reason || '')) errs.push(`reason "${res.reason}" does not match ${exp.reason}`);
  if (exp.login && res.login !== exp.login) errs.push(`login=${res.login} (expected ${exp.login})`);
  if (exp.strategy && res.strategy !== exp.strategy) errs.push(`strategy=${res.strategy} (expected ${exp.strategy})`);
  for (const [m, subs] of Object.entries(exp.modules || {})) {
    const got = (res.modules || []).find(x => x.name === m);
    if (!got) { errs.push(`module "${m}" missing (got: ${(res.modules || []).map(x => x.name).join(', ')})`); continue; }
    for (const s of subs) if (!got.subs.some(x => x.name === s)) errs.push(`"${m}" lacks sub-module "${s}" (got: ${got.subs.map(x => x.name).join(', ')})`);
  }
  for (const a of exp.absent || []) if ((res.modules || []).some(x => x.name === a)) errs.push(`"${a}" should not be a module`);
  return errs;
}

(async () => {
  const only = process.argv.includes('--only') ? process.argv[process.argv.indexOf('--only') + 1].toLowerCase() : null;
  require('fs').writeFileSync(ETAIL_LOGIN, "## Sign in to the admin panel\nNavigate to {{base_url}}.\nAssert the page title is 'Logimax | Login'.\n" +
    "Type {{username}} into the User Name field.\nType {{password}} into the Password field.\nSelect '{{branch}}' from the Select Branch dropdown.\n" +
    "Click the Sign In button.\nAssert the page title is 'Dashboard | Logimax'.\n");
  const server = http.createServer((req, res) => {
    const name = decodeURIComponent(req.url.split('?')[0].replace(/\/$/, '').split('/').pop());   // "x.html/" and "x.html/y.html" too (base URLs get a trailing slash)
    if (FILES[name]) { res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); res.end(FILES[name]); }
    else { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end('<!doctype html><title>page</title><p>' + name); }   // menu targets
  });
  const servePort = process.argv.includes('--serve') ? Number(process.argv[process.argv.indexOf('--serve') + 1]) : 0;
  await new Promise(r => server.listen(servePort, '127.0.0.1', r));
  if (servePort) { console.log(`Serving the sample apps on http://127.0.0.1:${servePort}/ (Ctrl+C to stop)`); return; }   // for manual / UI testing
  const base = `http://127.0.0.1:${server.address().port}/`;
  const browser = await chromium.launch({ headless: true });
  let failed = 0, ran = 0;
  for (const [name, file, opt, exp] of CASES) {
    if (only && !name.toLowerCase().includes(only)) continue;
    ran++;
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const pg = await ctx.newPage();
    const vars = { base_url: base + file, username: 'tester', password: 'secret123', branch: opt.branch || '' };
    const t0 = Date.now();
    let res;
    try {
      res = await readMenu(pg, { vars, loginSteps: opt.loginSteps, etailLogin: opt.etailLogin ? ETAIL_LOGIN : null, menuUrl: opt.menuUrl ? base + opt.menuUrl : undefined });
    } catch (e) { res = { ok: false, reason: 'crashed: ' + e.message }; }
    const errs = check(res, exp);
    failed += errs.length ? 1 : 0;
    const summary = res.ok ? `${res.strategy}: ${res.modules.length} modules, ${res.modules.reduce((a, m) => a + m.subs.length, 0)} subs` : `${res.stage}: ${(res.reason || '').slice(0, 70)}`;
    console.log(`${errs.length ? 'FAIL' : 'PASS'}  ${name.padEnd(48)} ${String(Date.now() - t0).padStart(6)} ms  ${summary}`);
    errs.forEach(e => console.log('        - ' + e));
    await ctx.close();
  }
  await browser.close(); server.close();
  require('fs').unlinkSync(ETAIL_LOGIN);
  console.log(`\n${ran - failed} of ${ran} sample checks passed.`);
  process.exit(failed ? 1 : 0);
})();
