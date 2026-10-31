// Runs the dashboard's real page script against a live server using a minimal fake DOM.
// usage: node tests/ui_smoke.js http://127.0.0.1:8765
const fs = require('fs'), path = require('path'), vm = require('vm');
const BASE = process.argv[2];
const html = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const ids = [...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]);

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.className = ''; this.dataset = {}; this.style = {}; this._text = ''; this._html = '';
    this.disabled = false; this.value = ''; this.checked = false; this.onclick = null; this.onchange = null; this.scrollTop = 0; this.clientHeight = 0; this.scrollHeight = 0; }
  set textContent(v) { this.children = []; this._text = String(v); this._html = ''; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set innerHTML(v) { this.children = []; this._text = ''; this._html = String(v); }
  get innerHTML() { return this._html; }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { cs.forEach(c => this.appendChild(typeof c === 'string' ? Object.assign(new El('#text'), {_text: c}) : c)); }
}
const els = {}; ids.forEach(i => els[i] = new El('div'));
els.faults.children = [];
const document = {
  getElementById: id => { if (!els[id]) throw new Error('missing element #' + id); return els[id]; },
  createElement: t => new El(t),
  createTextNode: t => Object.assign(new El('#text'), {_text: t}),
  querySelectorAll: sel => sel === '#faults button' ? els.faults.children.filter(c => c.tag === 'button') : [],
};
const realFetch = fetch;
const ctx = vm.createContext({document, fetch: (p, o) => realFetch(BASE + p, o), setTimeout, setInterval, clearInterval, console, JSON, Math});
const fail = m => { console.error('UI SMOKE FAIL:', m); process.exit(1); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const assert = (c, m) => { if (!c) fail(m); console.log('  ok -', m); };

(async () => {
  vm.runInContext(script, ctx);
  await sleep(400);
  assert(els.scn.children.length === 18, 'scenario dropdown has live + 17 scenarios');
  assert(els.agent.children.length === 3, 'agent dropdown has 3 agents');
  assert(els.faults.children.filter(c => c.tag === 'button').length === 9, 'nine fault buttons rendered');
  assert(els.kUnrec.textContent === '-' && els.kSync.textContent === '-', 'quiet-failure counters render dashes when idle');
  assert(els.kStr.textContent === '-', 'idle state renders dashes');

  els.scn.value = 'h1'; await els.btnStart.onclick();
  assert(parseInt(els.kStr.textContent) > 0, 'stranded customers shown after starting h1 (' + els.kStr.textContent + ')');
  assert(els.chart.innerHTML.includes('<svg'), 'chart rendered');
  assert(els.logs.children.length > 0, 'logs rendered');
  assert(els.btnPage.disabled === false, 'page button enabled');

  els.agent.value = 'rules_full'; els.auto.checked = false; await els.btnPage.onclick();
  assert(els.btnStep.disabled === false, 'step enabled after paging');
  let guard = 0;
  while (guard++ < 40 && els.approvals.children.length === 0) await els.btnStep.onclick();
  assert(els.approvals.children.length > 0, 'approval card appears for a risky action');
  const row = els.approvals.children[els.approvals.children.length - 1];
  assert(row.children.some(c => c.tag === 'button'), 'approve/deny buttons present');
  await row.children[0].onclick();               // Approve (starts auto-run)
  for (let i = 0; i < 120 && els.verdict.children.length === 0; i++) {
    if (els.approvals.children.length) { await els.approvals.children[els.approvals.children.length - 1].children[0].onclick(); }
    await sleep(250);
  }
  assert(els.verdict.children.length > 0, 'final verdict banner rendered');
  assert(els.verdict.children[0].className.includes('banner ok'), 'verdict says recovered and verified');
  assert(els.cov.children.length === 8, 'eight slide requirements listed');
  const met = els.cov.children.filter(r => r.children[0].className.includes(' t')).length;
  assert(met === 8, 'all 8 slide requirements met (' + met + '/8)');
  assert(els.kStr.textContent === '0', 'stranded customers back to 0');
  els.presenter.checked = true; await els.presenter.onchange();
  assert(els.truthcard.style.display === 'block', 'presenter view shows ground truth');

  // quiet failures: nothing shouts, but the reconciliation counters disagree
  els.scn.value = 'd9'; await els.btnStart.onclick();
  const gw = els.kGw.textContent.match(/^(\d+) \/ (\d+)$/);
  assert(gw && +gw[1] > +gw[2], 'gateway captured > recorded payments on d9 (' + els.kGw.textContent + ')');
  assert(parseInt(els.kUnrec.textContent) > 0, 'payments never recorded shown (' + els.kUnrec.textContent + ')');
  els.scn.value = 'd10'; await els.btnStart.onclick();
  assert(parseInt(els.kSync.textContent) > 0, 'out-of-sync ticket records shown (' + els.kSync.textContent + ')');
  assert(els.btnSpike.disabled === false && els.btnSpike.textContent.startsWith('Traffic spike'), 'spike button available');
  await els.btnSpike.onclick();
  assert(els.btnSpike.textContent === 'Back to normal traffic', 'spike toggles on');

  // escalation: the agent that cannot fix a novel fault hands over instead of claiming success
  els.scn.value = 'h4'; await els.btnStart.onclick();
  els.agent.value = 'rules_full'; els.auto.checked = true; await els.btnPage.onclick();
  for (let i = 0; i < 80 && els.verdict.children.length === 0; i++) await els.btnStep.onclick();
  assert(els.verdict.children.length > 0 && els.verdict.children[0].className.includes('banner warn'), 'escalation shown as a warning, not success');
  assert(els.verdict.children.some(c => c.className === 'handoff'), 'hand-off report rendered for the human');
  console.log('UI SMOKE PASSED');
  process.exit(0);
})().catch(e => fail(e.stack || e));
