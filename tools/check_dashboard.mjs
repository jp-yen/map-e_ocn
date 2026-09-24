import { chromium } from 'playwright';

(async () => {
  console.log('Launching headless Chromium...');
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];

  page.on('console', msg => {
    if (msg.type() === 'error') {
      errors.push(`[Console Error] ${msg.text()}`);
    }
  });

  page.on('pageerror', err => {
    errors.push(`[Uncaught Exception] ${err.message}`);
  });

  console.log('Navigating to http://localhost:1600/ ...');
  try {
    await page.goto('http://localhost:1600/', { waitUntil: 'networkidle', timeout: 10000 });
    const screenshotPath = 'dashboard_screenshot.png';
    await page.screenshot({ path: screenshotPath, fullPage: true });
    console.log(`Saved screenshot: ${screenshotPath}`);

    if (errors.length > 0) {
      console.error('\n❌ Detected Frontend Errors:');
      errors.forEach(e => console.error(e));
      process.exit(1);
    } else {
      console.log('\n✅ Dashboard loaded successfully with 0 frontend errors.');
    }
  } catch (e) {
    console.error(`Failed to load page: ${e.message}`);
    process.exit(1);
  } finally {
    await browser.close();
  }
})();
