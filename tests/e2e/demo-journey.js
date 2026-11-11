// AURIX demo journey: wallet -> faucet -> send -> escrow lock/release -> PayPal (simulator) ->
// conditional USD payment released by signed delivery evidence (MVP 0.2) -> explorer.
// E2E_VOID=1 also runs the 2-minute demo deadline (void, buyer not charged).
// Usage: BASE_URL=http://localhost:8080 SHOT_DIR=/tmp/shots node tests/e2e/demo-journey.js
// NODES (optional, comma separated chain REST URLs) is passed as ?nodes= so the wallet reads and
// broadcasts directly against the nodes (the gateway is then only used for faucet, PayPal, Guardian).
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { chromium } = require('playwright');

const BASE_URL = (process.env.BASE_URL || 'http://localhost:8080').replace(/\/$/, '');
const SHOT_DIR = process.env.SHOT_DIR || '.';
const NODES = process.env.NODES || '';
const RECIPIENT = 'aur12zc3mwptt9l78ljkeehal5ssjmht6gunwaaclt';
const toastMatches = re => document.getElementById('toast').textContent.match(new RegExp(re));
const step = msg => console.log(`[e2e] ${msg}`);

async function main() {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const browser = await chromium.launch();
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
    page.on('pageerror', e => errors.push(e.message));

    const url = BASE_URL + '/app.html' + (NODES ? `?nodes=${encodeURIComponent(NODES)}` : '');
    step(`open ${url}`);
    await page.goto(url);
    if (NODES) {
      await page.waitForFunction(() => /block #\d+/.test(document.getElementById('chainStatus').textContent));
      const via = await page.getAttribute('#chainStatus', 'title');
      step(`chain status ${via}`);
      if (!/URL \?nodes=/.test(via || '')) throw new Error(`wallet not using ?nodes= endpoint (${via})`);
    }

    step('create wallet and claim faucet');
    await page.click('#createWallet');
    await page.click('#faucet');
    await page.waitForFunction(() => document.getElementById('balAur').textContent.startsWith('100'), null, { timeout: 15000 });

    step('send 80 AUR (Guardian check first)');
    await page.click('nav [data-tab=send]');
    await page.fill('#sendTo', RECIPIENT);
    await page.fill('#sendAmt', '80');
    await page.click('#sendCheck');
    await page.waitForSelector('#sendGo');
    step(`guardian risk score: ${(await page.textContent('.risk .score')).trim()}`);
    await page.click('#sendGo');
    await page.waitForFunction(toastMatches, 'Confirmed');

    step('lock 5 AUR in escrow');
    await page.click('nav [data-tab=escrow]');
    await page.fill('#escTo', RECIPIENT);
    await page.fill('#escAmt', '5');
    await page.click('#escCheck');
    await page.waitForSelector('#escGo');
    await page.click('#escGo');
    await page.waitForFunction(toastMatches, 'locked');

    step('release escrow');
    await page.waitForSelector('#escrowGrid button');
    await page.click('#escrowGrid button:has-text("Release")');
    await page.waitForFunction(toastMatches, 'released');

    step('PayPal: create order, capture refused before payment');
    await page.click('nav [data-tab=paypal]');
    await page.click('#ppCreate');
    await page.waitForSelector('#ppGo');
    await page.click('#ppGo');
    await page.waitForSelector('#ppMsg >> text=not approved');

    step('PayPal: buyer approves on simulator page');
    const [payPage] = await Promise.all([page.context().waitForEvent('page'), page.click('#ppLink')]);
    await payPage.click('#pay');
    await payPage.waitForSelector('text=Payment approved');
    await payPage.close();

    step('PayPal: capture and anchor proof on chain');
    await page.click('#ppGo');
    await page.waitForSelector('text=Captured & anchored', { timeout: 20000 });
    await page.screenshot({ path: `${SHOT_DIR}/e2e-paypal.png` });

    step('Conditional USD: create order, buyer approves, PayPal holds the funds');
    await page.click('nav [data-tab=cond]');
    await page.click('#cdCreate');
    await page.waitForSelector('#cdAuth');
    const oid = (await page.textContent('#cdResult code')).trim();
    const [condPay] = await Promise.all([page.context().waitForEvent('page'), page.click('#cdLink')]);
    await condPay.click('#pay');
    await condPay.waitForSelector('text=Payment approved');
    await condPay.close();
    await page.click('#cdAuth');
    await page.waitForSelector('text=Funds held by PayPal');

    step('Conditional USD: forged delivery refused, signed delivery releases the payment');
    await page.click('#cdForge');
    await page.waitForFunction(toastMatches, 'refused');
    await page.click('#cdDeliver');
    await page.waitForSelector('text=Released: captured on evidence, proof anchored', { timeout: 40000 });

    step('Conditional USD: AI advisor explanation (advisory only)');
    await page.click('#cdExplain');
    await page.waitForSelector('.advice');
    await page.screenshot({ path: `${SHOT_DIR}/e2e-conditional.png`, fullPage: true });

    step('Conditional USD: proof package downloaded and verified offline');
    const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#cdProof')]);
    const zip = path.join(SHOT_DIR, `aurix-proof-${oid}.zip`);
    await dl.saveAs(zip);
    const py = path.resolve(__dirname, '../../verifier/.venv/bin/python');
    if (fs.existsSync(py)) {
      const keys = path.join(SHOT_DIR, 'escrow-keys.json');
      fs.writeFileSync(keys, await (await fetch(`${BASE_URL}/api/escrow/keys`)).text());
      const out = execFileSync(py, ['-m', 'aurix_verify', zip, '--keys', keys], { encoding: 'utf8' });
      if (!/: VALID/.test(out) || !/replayed RELEASE \(match\)/.test(out)) throw new Error(`proof not valid:\n${out}`);
      step('aurix-verify: VALID, decision replayed');
    } else step('aurix-verify not installed (verifier/.venv): verification skipped');

    step('Conditional USD: verify in the browser, then tamper one byte -> INVALID');
    await page.click('#cdVerify');
    await page.waitForSelector('#cdReport >> text=VALID · order');
    await page.click('#cdTamper');
    await page.waitForSelector('#cdReport >> text=INVALID · order');

    if (process.env.E2E_VOID === '1') {
      step('Conditional USD: 2-minute demo deadline without delivery -> voided');
      await page.selectOption('#cdDays', 'm2');
      await page.click('#cdCreate');
      await page.waitForSelector('#cdAuth');
      const [p2] = await Promise.all([page.context().waitForEvent('page'), page.click('#cdLink')]);
      await p2.click('#pay'); await p2.waitForSelector('text=Payment approved'); await p2.close();
      await page.click('#cdAuth');
      await page.waitForSelector('text=Voided: the buyer was not charged', { timeout: 200000 });
    }

    step('explorer');
    await page.click('nav [data-tab=explorer]');
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${SHOT_DIR}/e2e-explorer.png`, fullPage: true });

    if (errors.length) throw new Error(`page errors: ${errors.join('; ')}`);
    step(`PASS (screenshots in ${SHOT_DIR})`);
  } finally {
    await browser.close();
  }
}

main().catch(e => {
  console.error(`[e2e] FAIL: ${e.message}`);
  process.exit(1);
});
