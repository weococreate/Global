#!/usr/bin/env node
// 抓澳洲央行（RBA）現金利率目標的調整紀錄，輸出 JSON：[{"date":"YYYY-MM-DD","rate":4.6}, ...]（舊到新）。
// RBA 官網擋一般程式連線（curl／urllib 皆 403），只有真瀏覽器能開，所以用 Playwright 的 Chromium。
// 由 fetch_official_rates.py 呼叫；失敗時以非 0 結束，呼叫端只略過 AUD，不擋其他幣別。
const { chromium } = require('playwright');

const URL = 'https://www.rba.gov.au/statistics/cash-rate/';
const MONTHS = { Jan: 1, Feb: 2, Mar: 3, Apr: 4, May: 5, Jun: 6, Jul: 7, Aug: 8, Sep: 9, Oct: 10, Nov: 11, Dec: 12 };

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    const resp = await page.goto(URL, { timeout: 40000, waitUntil: 'domcontentloaded' });
    if (!resp || resp.status() !== 200) throw new Error(`HTTP ${resp && resp.status()}`);
    // 每列：生效日 | 變動 | 調整後利率 | ...；只取前 20 列（約 3 年）就夠補 BIS 的延遲
    const rows = await page.$$eval('table tbody tr', trs =>
      trs.slice(0, 20).map(tr => [...tr.querySelectorAll('th, td')].map(c => c.innerText.trim())));
    const out = [];
    for (const cells of rows) {
      const m = /^(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})$/.exec(cells[0] || '');
      const rate = parseFloat(cells[2]);
      if (!m || !MONTHS[m[2]] || Number.isNaN(rate)) continue;
      const d = `${m[3]}-${String(MONTHS[m[2]]).padStart(2, '0')}-${m[1].padStart(2, '0')}`;
      out.push({ date: d, rate });
    }
    if (!out.length) throw new Error('表格解析不到任何列（版面可能改了）');
    out.sort((a, b) => a.date.localeCompare(b.date));
    process.stdout.write(JSON.stringify(out));
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(`[fetch_rba] ${e.message}`); process.exit(1); });
