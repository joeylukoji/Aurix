// AURIX demo journey: wallet -> faucet -> send -> escrow lock/release -> PayPal (simulator) -> explorer.
// Usage: BASE_URL=http://localhost:8080 SHOT_DIR=/tmp/shots node tests/e2e/demo-journey.js
const fs = require('fs');
const { chromium } = require('playwright');

const BASE_URL = (process.env.BASE_URL || 'http://localhost:8080').replace(/\/$/, '');
const SHOT_DIR = process.env.SHOT_DIR || '.';
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

    step(`open ${BASE_URL}`);
    await page.goto(BASE_URL + '/');

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
