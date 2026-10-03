'use strict';
// GlobalRates 雲端畫面檢查（2026-10-02）：項目沿用 tests/globalrates_macro.spec.ts，加上手機／電腦兩尺寸與連外限制。
// 用法：node browser-check.cjs <GlobalRates.html 路徑>；成功才存 preview-*.png（與 HTML 同資料夾）。
const {chromium}=require('playwright');
const {pathToFileURL}=require('node:url');
const path=require('node:path');
const fs=require('node:fs');
const GA4_SRC='https://www.googletagmanager.com/gtag/js?id=G-JVW9ZBDT3Y';
const CHART_SRC='https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
(async()=>{
  const file=path.resolve(process.argv[2]||'');
  if(!fs.existsSync(file))throw new Error('找不到要檢查的網頁：'+file);
  const browser=await chromium.launch({headless:true});
  try{
    for(const width of [390,1280]){
      const page=await browser.newPage({viewport:{width,height:900}});const errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
      // 只放行 Chart.js（jsDelivr，網頁本身帶 SRI）；GA4 回空內容；其他連外一律算錯。
      await page.route(/^https?:/,r=>{const u=r.request().url();if(u===CHART_SRC)return r.continue();if(u===GA4_SRC)return r.fulfill({status:200,contentType:'application/javascript',body:''});errors.push('不應連外：'+u);return r.abort();});
      await page.goto(pathToFileURL(file).href);
      await page.click('[data-view="macro"]');
      await page.locator('#view-macro').waitFor({state:'visible'});
      const check=await page.evaluate(()=>({
        matrix:document.querySelectorAll('#spread-matrix-table tbody tr').length,
        hintsCHF:(document.querySelector('#macro-hints')?.textContent||'').includes('CHF'),
        changes:document.querySelectorAll('#yield-changes-table tbody tr').length,
        fred:(document.querySelector('#macro-fred')?.textContent||''),
        macro:typeof GLOBALRATES_DATA!=='undefined'&&GLOBALRATES_DATA.macro&&GLOBALRATES_DATA.macro.available===true,
        overflow:document.documentElement.scrollWidth>innerWidth+2}));
      const fredOk=check.macro?(check.fred.includes('CPI 年增率')&&check.fred.includes('實質政策利率')):check.fred.includes('FRED_API_KEY');
      if(errors.length||check.matrix!==12||!check.hintsCHF||check.changes<1||!fredOk||check.overflow)
        throw new Error(JSON.stringify({width,errors,check:{...check,fred:check.fred.slice(0,60)}}));
      await page.screenshot({path:path.join(path.dirname(file),`preview-${width}.png`),fullPage:true});
      console.log(JSON.stringify({width,matrix:check.matrix,changes:check.changes,macro:check.macro,overflow:check.overflow}));
      await page.close();
    }
  }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
