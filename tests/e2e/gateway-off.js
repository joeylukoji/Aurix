// AURIX: the wallet keeps working without the gateway, and survives the loss of a chain node.
// The wallet is served as static files (like a CDN deployment), talks to the chain directly via
// ?nodes=node0,node1 and to the gateway (faucet) cross-origin (CORS).
//   1. faucet via the gateway, 2. stop the gateway (RUN_DIR/gateway.pid): the UI shows the
//   gateway-off notice; send + escrow create/release still work, 3. kill node0 (RUN_DIR/chain0.pid):
//   the wallet fails over to node1 and a send still works.
// Env: GATEWAY_URL, NODES (node0,node1 REST), RUN_DIR, WEB_PORT (static server), SHOT_DIR.
// DESTRUCTIVE for the running stack: run it last.
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { chromium } = require('playwright');

const GATEWAY_URL = (process.env.GATEWAY_URL || 'http://localhost:8080').replace(/\/$/, '');
const NODES = process.env.NODES || 'http://localhost:1317,http://localhost:2317';
const RUN_DIR = process.env.RUN_DIR || path.join(__dirname, '../../.run');
const WEB_PORT = Number(process.env.WEB_PORT || 8070);
const SHOT_DIR = process.env.SHOT_DIR || '.';
const RECIPIENT = 'aur12zc3mwptt9l78ljkeehal5ssjmht6gunwaaclt';
const step = msg => console.log(`[e2e-gw-off] ${msg}`);
const toastMatches = re => document.getElementById('toast').textContent.match(new RegExp(re));

function killPid(name) {
  const f = path.join(RUN_DIR, `${name}.pid`);
  const pid = Number(fs.readFileSync(f, 'utf8').trim());
  process.kill(pid, 'SIGTERM');
  step(`killed ${name} (pid ${pid})`);
}

async function waitHttp(url, ms = 10000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try { if ((await fetch(url)).ok) return; } catch {}
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error(`${url} not reachable`);
}

async function send(page, amount) {
  await page.click('nav [data-tab=send]');
  await page.fill('#sendTo', RECIPIENT);
  await page.fill('#sendAmt', String(amount));
  await page.click('#sendCheck');
  await page.waitForSelector('#sendGo');
  await page.click('#sendGo');
  await page.waitForFunction(toastMatches, 'Confirmed', { timeout: 40000 });
}

async function main() {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const web = spawn('python3', ['-m', 'http.server', String(WEB_PORT), '--bind', '127.0.0.1',
    '--directory', path.join(__dirname, '../../web')], { stdio: 'ignore' });
  fs.writeFileSync(path.join(RUN_DIR, 'webstatic.pid'), String(web.pid));
  const browser = await chromium.launch();
  const errors = [];
  try {
    await waitHttp(`http://127.0.0.1:${WEB_PORT}/index.html`);
    const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
    page.on('pageerror', e => errors.push(e.message));
    const url = `http://127.0.0.1:${WEB_PORT}/app.html?nodes=${encodeURIComponent(NODES)}`;
    step(`open static wallet ${url}`);
    await page.goto(url);

    step(`configure gateway ${GATEWAY_URL} (cross-origin)`);
    await page.click('nav [data-tab=settings]');
    await page.fill('#gwUrl', GATEWAY_URL);
    await page.click('#saveGw');
    await page.waitForFunction(toastMatches, 'Gateway connected');

    step('create wallet and claim faucet through the gateway');
    await page.click('nav [data-tab=wallet]');
    await page.click('#createWallet');
    await page.click('#faucet');
    await page.waitForFunction(() => document.getElementById('balAur').textContent.startsWith('100'), null, { timeout: 20000 });

    step('stop the gateway');
    killPid('gateway');
    await page.waitForFunction(() => { const g = document.getElementById('gwStatus');
      return !g.hidden && /gateway off/.test(g.textContent); }, null, { timeout: 20000 });
    step(`UI notice: "${(await page.textContent('#gwStatus')).trim()}"`);
    await page.screenshot({ path: `${SHOT_DIR}/e2e-gateway-off.png` });

    step('send 10 AUR without gateway');
    await send(page, 10);

    step('escrow create + release without gateway');
    await page.click('nav [data-tab=escrow]');
    await page.fill('#escTo', RECIPIENT);
    await page.fill('#escAmt', '5');
    await page.click('#escCheck');
    await page.waitForSelector('#escGo');
    await page.click('#escGo');
    await page.waitForFunction(toastMatches, 'locked', { timeout: 40000 });
    await page.waitForSelector('#escrowGrid button:has-text("Release")');
    await page.click('#escrowGrid button:has-text("Release")');
    await page.waitForFunction(toastMatches, 'released', { timeout: 40000 });

    step('kill node0: the wallet must fail over to node1');
    killPid('chain0');
    await send(page, 3);
    await page.waitForFunction(() => /node1|:2\d{3}|URL/.test(document.getElementById('chainStatus').title) &&
      /block #\d+/.test(document.getElementById('chainStatus').textContent), null, { timeout: 15000 });
    const active = await page.evaluate(() => window.AurixChain.activeEndpoint && window.AurixChain.activeEndpoint.rest);
    const node0 = NODES.split(',')[0];
    step(`active endpoint after failover: ${active}`);
    if (!active || active === node0) throw new Error(`wallet still on node0 (${active})`);
    await page.click('nav [data-tab=wallet]');
    await page.click('#refresh');
    await page.waitForFunction(() => document.getElementById('balAur').textContent.startsWith('81.9'), null, { timeout: 15000 });
    step(`balance ${(await page.textContent('#balAur')).trim()}`);
    await page.screenshot({ path: `${SHOT_DIR}/e2e-failover.png` });

    if (errors.length) throw new Error(`page errors: ${errors.join('; ')}`);
    step('PASS');
  } finally {
    await browser.close();
    web.kill();
  }
}

main().catch(e => {
  console.error(`[e2e-gw-off] FAIL: ${e.message}`);
  process.exit(1);
});
