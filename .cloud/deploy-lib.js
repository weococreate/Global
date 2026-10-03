'use strict';
/**
 * deploy-lib.js — build-*-deploy.js 共用函式庫（2026-07-12 架構體檢抽出）
 *
 * 統一「開發版 XXX1.html → 單一自足部署版 XXX.html」的打包原語，重點是防呆：
 *   - sub()：錨點「找不到」與「不唯一」都直接 throw——杜絕錨點重複時靜默產出半殘部署檔。
 *   - replaceRange()：起訖錨點切區塊時，驗證切出來的內容長相（mustContain/maxLen），
 *     杜絕 "})();" 這類常見片段當結尾錨點切錯範圍。
 *   - escapeScriptEnd()：資料內容若含 "</script>" 字面值會提早終結 inline script 吞掉整份文件
 *     （feedback_devlog_html_escaping），內嵌前一律轉義。
 *   - writeOut()：輸出後掃描殘留外部相依（<script src>／動態載入殘跡），預設全禁、可白名單。
 *
 * 各 app 的 build-*-deploy.js 只留：讀檔、錨點設定、呼叫這裡的原語。
 * 新 app 一律採 __DEV_LOADER_START/END__ ＋ __DEPLOY_BOOT__ 標記制（見 markers()）。
 */
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;

/** 讀 UTF-8 檔（相對於 beyybot-agent 根目錄） */
const R = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

/** 資料內嵌進 <script> 前的必要轉義（防 "</script>" 字面值吞文件） */
const escapeScriptEnd = (js) => js.replace(/<\/script>/gi, '<\\/script>');

/** 唯一錨點替換：找不到、或出現兩次以上，一律 throw（不靜默產出半殘檔） */
function sub(h, needle, repl) {
  const i = h.indexOf(needle);
  if (i < 0) throw new Error('找不到替換錨點: ' + needle.slice(0, 80));
  if (h.indexOf(needle, i + 1) !== -1) throw new Error('替換錨點不唯一: ' + needle.slice(0, 80));
  return h.slice(0, i) + repl + h.slice(i + needle.length);
}

/** regex 替換：要求恰好命中一次；用函式替換器避免 $& 等被特殊解讀 */
function subRe(h, re, repl) {
  const g = new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g');
  const n = (h.match(g) || []).length;
  if (n !== 1) throw new Error('regex 錨點命中 ' + n + ' 次（需恰好 1）: ' + re.source.slice(0, 80));
  return h.replace(re, () => repl);
}

/**
 * 起訖錨點區塊替換（japanpowder／taiwantrails 型）。
 * 防呆：start 必須唯一；end 取 start 之後第一個；切出的區塊必須包含 opts.mustContain
 * （證明切到的是預期內容）且長度 ≤ opts.maxLen（預設 4000，防切過頭吞掉後續程式）。
 */
function replaceRange(h, startNeedle, endNeedle, repl, opts = {}) {
  const s = h.indexOf(startNeedle);
  if (s < 0) throw new Error('找不到區塊起點: ' + startNeedle.slice(0, 80));
  if (h.indexOf(startNeedle, s + 1) !== -1) throw new Error('區塊起點不唯一: ' + startNeedle.slice(0, 80));
  const e = h.indexOf(endNeedle, s + startNeedle.length);
  if (e < 0) throw new Error('起點之後找不到區塊終點: ' + endNeedle.slice(0, 80));
  const seg = h.slice(s, e + endNeedle.length);
  const maxLen = opts.maxLen || 4000;
  if (seg.length > maxLen) throw new Error(`切出的區塊 ${seg.length} 字元 > ${maxLen}，疑似終點錨點抓錯（吞掉後續程式）`);
  for (const mc of [].concat(opts.mustContain || [])) {
    if (!seg.includes(mc)) throw new Error('切出的區塊不含預期內容「' + mc + '」，疑似錨點抓錯');
  }
  return h.slice(0, s) + repl + h.slice(e + endNeedle.length);
}

/** 內嵌所有 <script src="vendor/..."> 與 <link href="vendor/...">（函式替換器防 $ 誤解讀） */
function inlineVendors(h, opts = {}) {
  let n = 0;
  // 2026-09-26：網頁搬進 apps/<代號>/ 後寫成 ../../vendor/…；前綴只影響網頁位置，vendor 一律從專案根目錄讀
  h = h.replace(/<script src="(?:\.\.\/)*(vendor\/[^"]+)"><\/script>/g, (_m, src) => { n++; return '<script>' + R(src) + '</script>'; });
  h = h.replace(/<link rel="stylesheet" href="(?:\.\.\/)*(vendor\/[^"]+)">/g, (_m, src) => { n++; return '<style>' + R(src) + '</style>'; });
  if (opts.expect !== undefined && n !== opts.expect) throw new Error(`vendor 內嵌 ${n} 個（預期 ${opts.expect}）`);
  return h;
}

/**
 * 標準三標記部署契約（taiwan-data-app skill／scaffold.sh 同款）：
 *   /* __DEV_LOADER_START__ *／…／* __DEV_LOADER_END__ *／ → 內嵌資料
 *   /* __DEPLOY_BOOT__ *／ → boot 呼叫
 * HTML 註解型標記（<!-- __DEV_LOADER_START__ -->，globalpeaks 用）也支援。
 */
function markers(h, dataJs, opts = {}) {
  const boot = opts.boot === undefined ? 'boot();' : opts.boot;
  // opts.appendJs：跟資料一起內嵌的額外 JS（如 globalpeaks 的 window.WORLD_TOPO=...）
  const payload = escapeScriptEnd(dataJs + (opts.appendJs || ''));
  const pairs = [
    ['/* __DEV_LOADER_START__ */', '/* __DEV_LOADER_END__ */', false], // JS 註解型：標記在 <script> 內，直接嵌 JS
    ['<!-- __DEV_LOADER_START__ -->', '<!-- __DEV_LOADER_END__ -->', true], // HTML 註解型：標記在 HTML 層，需自帶 <script> 包裝
  ];
  let done = false;
  for (const [S, E, wrap] of pairs) {
    const i = h.indexOf(S);
    if (i < 0) continue;
    const j = h.indexOf(E, i);
    if (j < 0) throw new Error('有 START 標記但找不到 END 標記（勿刪 __DEV_LOADER_END__）');
    const repl = wrap ? '<script>\n' + payload + '\n</script>' : payload;
    h = h.slice(0, i) + repl + h.slice(j + E.length);
    done = true;
    break;
  }
  if (!done) throw new Error('找不到 dev loader 標記，無法打包（勿刪 __DEV_LOADER_START/END__）');
  if (boot !== null) h = sub(h, '/* __DEPLOY_BOOT__ */', boot);
  return h;
}

/**
 * 輸出部署檔＋自足性把關。
 * opts.allowExternal：允許殘留的外部 <script src>（如 japanpowder 的 Leaflet CDN），逐一列白名單子字串。
 * opts.forbid：額外禁止殘留的子字串（如 'xxx-data.js?t='）。
 */
function writeOut(outName, h, opts = {}) {
  const allow = opts.allowExternal || [];
  const srcTags = h.match(/<script[^>]+src="[^"]*"/g) || [];
  const bad = srcTags.filter((t) => !allow.some((a) => t.includes(a)));
  if (bad.length) throw new Error('產物殘留外部/未內嵌 script：' + bad.join(' | ').slice(0, 300));
  for (const f of [].concat(opts.forbid || [])) {
    if (h.includes(f)) throw new Error('產物殘留禁止內容「' + f + '」（動態載入未內嵌？）');
  }
  const out = path.join(ROOT, outName);
  fs.writeFileSync(out, h);
  const kb = (fs.statSync(out).size / 1024).toFixed(0);
  console.log(`→ ${outName} 產出（單一自足，${kb} KB${allow.length ? '，允許外部：' + allow.join('、') : '，無外部相依'}）`);
}

module.exports = { R, sub, subRe, replaceRange, inlineVendors, markers, escapeScriptEnd, writeOut, ROOT };
