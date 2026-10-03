#!/usr/bin/env node
/** apps/GlobalRates/build-globalrates-deploy.js — apps/GlobalRates/GlobalRates1.html ＋ apps/GlobalRates/globalrates-data.js → 單一自足 apps/GlobalRates/GlobalRates.html
 *
 *  與其他 14 支 build-*-deploy.js 一致，走共用 deploy-lib.js（錨點唯一性把關）。
 *  GlobalRates 特性：
 *    - 資料以舊式單標記 <script src="apps/GlobalRates/globalrates-data.js"></script> 外掛（非 __DEV_LOADER__ 三標記制）。
 *    - Chart.js 走 jsdelivr CDN＋SRI（刻意保留、非漏網），故 allowExternal 放行 cdn.jsdelivr.net。
 *    - 發佈檔不引用內部腳本檔名（防洩漏 internal-doc）：去掉資料檔首行「// 自動產生…來源：…」註解。
 *
 *  --check：只驗「快照是否已與開發版同步」，不覆寫；落後即非零退出（供 CI／收工自檢／bey gr-snapshot --check）。
 *
 *  2026-07-15 收斂：本檔取代舊 make_globalrates_snapshot.py 與 deploy_all.py 內寫死的 regen hook，
 *  內嵌邏輯自此只有這一處（見 AutoDeploy自動部署計劃書.md §2）。 */
const fs = require('fs');
const path = require('path');
const { R, sub, escapeScriptEnd, writeOut, ROOT } = require('../../deploy-lib');

const SRC_TAG = '<script src="globalrates-data.js"></script>';
const OUT = 'apps/GlobalRates/GlobalRates.html';

let data = R('apps/GlobalRates/globalrates-data.js');
const nl = data.indexOf('\n');
if (data.startsWith('// 自動產生') && nl >= 0) data = data.slice(nl + 1);

const html = sub(R('apps/GlobalRates/GlobalRates1.html'), SRC_TAG, '<script>\n' + escapeScriptEnd(data) + '\n</script>');

if (process.argv.includes('--check')) {
  const outPath = path.join(ROOT, OUT);
  const cur = fs.existsSync(outPath) ? fs.readFileSync(outPath, 'utf8') : '';
  if (cur === html) { console.log('[OK] apps/GlobalRates/GlobalRates.html 快照已與開發版同步'); process.exit(0); }
  console.error('[FAIL] apps/GlobalRates/GlobalRates.html 快照落後於開發版，請執行 node apps/GlobalRates/build-globalrates-deploy.js 重產');
  process.exit(1);
}

// writeOut 做自足性把關（放行 jsdelivr CDN＋SRI）後寫出
writeOut(OUT, html, { allowExternal: ['cdn.jsdelivr.net'] });
