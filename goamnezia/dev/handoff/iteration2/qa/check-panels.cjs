// DOM-only prototype QA. No screenshots are captured or inspected.
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const dir=path.resolve(__dirname,'..'), base=process.env.GA_DESIGN_BASE || 'http://127.0.0.1:8768/iteration2/';
const source=fs.readFileSync(path.join(dir,'Panel.dc.html'),'utf8');
const meta=JSON.parse(source.match(/data-props="([^"]+)"/)[1].replaceAll('&quot;','"').replaceAll('&amp;','&').replaceAll('&#x27;',"'"));
(async()=>{
 const b=await chromium.launch({headless:true}), p=await b.newPage({viewport:{width:1440,height:920}});const errors=[],external=[];
 p.on('pageerror',e=>errors.push(e.message)); p.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
 p.on('request',r=>{if(!/^(http:\/\/127\.0\.0\.1|data:|blob:)/.test(r.url()))external.push(r.url())});
 await p.goto(base+'Panel.dc.html');await p.locator('[data-hero]').waitFor();await p.addStyleTag({content:'*{animation:none!important;transition:none!important}'});
 const rows=[];
 for(const cfg of [{device:'desktop',lang:'ru',theme:'light',width:1440,height:920},{device:'phone',lang:'en',theme:'dark',width:360,height:800}]){
  await p.setViewportSize({width:cfg.width,height:cfg.height});
  for(const screen of meta.screen.options){
   await p.evaluate(args=>window.__dcSetProps('Panel',args),{...cfg,screen,persist:false});
   await p.waitForTimeout(35);
   const dom=await p.evaluate(()=>{
    const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
    const btn=[...document.querySelectorAll('[data-hero][data-device="phone"] button')].filter(visible);
    const small=btn.filter(e=>{const r=e.getBoundingClientRect();return r.height<43.5||r.width<43.5}).map(e=>({text:e.innerText||e.getAttribute('aria-label'),w:Math.round(e.getBoundingClientRect().width),h:Math.round(e.getBoundingClientRect().height)}));
    const dialogs=[...document.querySelectorAll('[role="dialog"],[role="alertdialog"]')].filter(visible);
    return {chars:document.body.innerText.length,roots:document.querySelectorAll('[data-hero]').length,overflow:document.documentElement.scrollWidth>innerWidth+1,small,dialogCyrillic:dialogs.flatMap(e=>e.innerText.split('\n').filter(t=>/[А-Яа-яЁё]/.test(t))),errorNode:!!document.querySelector('[data-dc-error]')};
   });
   rows.push({screen,device:cfg.device,lang:cfg.lang,...dom});
  }
 }
 const report={at:new Date().toISOString(),states:meta.screen.options.length,checks:rows.length,errors:[...new Set(errors)],external:[...new Set(external)],rows};
 fs.writeFileSync(path.join(__dirname,'dom-results.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({states:report.states,checks:rows.length,errors:report.errors,external:report.external,empty:rows.filter(x=>x.chars<20).map(x=>x.screen),small:rows.filter(x=>x.small.length).map(x=>({s:x.screen,items:x.small.slice(0,5)})),overflow:rows.filter(x=>x.overflow).map(x=>x.screen),untranslated:rows.filter(x=>x.lang==='en'&&x.dialogCyrillic.length).map(x=>({s:x.screen,text:x.dialogCyrillic}))},null,2));
 await b.close();
})().catch(e=>{console.error(e);process.exit(1)});
