const {chromium}=require('playwright'), assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
const b=await chromium.launch({headless:true}),p=await b.newPage({viewport:{width:1440,height:920}}),errors=[],result={};p.on('pageerror',e=>errors.push(e.message));
const base=process.env.GA_DESIGN_BASE||'http://127.0.0.1:8768/iteration2/';
await p.goto(base+'Panel.dc.html?screen=overview&lang=en&persist=0');await p.locator('[data-hero]').waitFor();
await p.getByRole('button',{name:'Settings',exact:true}).first().click();await p.getByText('Panel access',{exact:true}).waitFor();
assert.match(p.url(),/screen=settings/);assert.equal(await p.locator('[data-hero]').count(),1);result.crossToSettings=true;
await p.getByRole('button',{name:/^Users/}).first().click();await p.getByRole('button',{name:'Add',exact:true}).first().waitFor();assert.match(p.url(),/screen=users/);result.crossToUsers=true;
await p.goBack();await p.getByText('Panel access',{exact:true}).waitFor();assert.match(p.url(),/screen=settings/);result.historyBack=true;
// Shared appearance and language survive component changes, even with persistence disabled.
await p.getByRole('button',{name:'Dark',exact:true}).click();assert.equal(await p.locator('[data-hero]').getAttribute('data-theme'),'dark');
await p.getByRole('button',{name:'Overview',exact:true}).first().click();assert.equal(await p.locator('[data-hero]').getAttribute('data-theme'),'dark');result.sharedTheme=true;
await p.getByRole('button',{name:'RU',exact:true}).click();await p.getByRole('button',{name:'Настройки',exact:true}).first().click();await p.getByText('Вход в панель',{exact:true}).first().waitFor();result.sharedLanguage=true;
await p.goto(base+'Panel.dc.html?screen=user-quota&lang=en&persist=0');await p.locator('[data-pup="dialog"]').getByRole('button',{name:'Save',exact:true}).click();const toast=await p.locator('[role="status"]').filter({hasText:'Quota for'}).innerText();assert(!/[А-Яа-яЁё]/.test(toast),toast);result.quotaToast=toast;
await p.goto(base+'Panel.dc.html?screen=user-reissue&lang=en&persist=0');await p.locator('[role="alertdialog"] input').fill('mama');await p.locator('[role="alertdialog"]').getByRole('button',{name:'Reissue',exact:true}).click();const reissue=await p.locator('[role="status"]').filter({hasText:'reissued'}).innerText();assert(!/[А-Яа-яЁё]/.test(reissue),reissue);result.reissueToast=reissue;

for(const spec of [{screen:'profiles-install',port:'52123',choice:'Custom'},{screen:'profiles-install-443-free',port:'443',choice:'UDP 443'}]){
 await p.goto(base+`Panel.dc.html?screen=${spec.screen}&lang=en&persist=0`);await p.locator('[data-pup="dialog"]').waitFor();assert.match(p.url(),new RegExp('screen='+spec.screen));
 const d=p.locator('[data-pup="dialog"]');await d.locator('button').filter({hasText:new RegExp('^'+spec.choice)}).click();if(spec.choice==='Custom')await d.locator('input[inputmode="numeric"]').fill(spec.port);
 await d.getByRole('button',{name:'Install',exact:true}).click();await p.getByText('AmneziaWG 1.5 installed',{exact:true}).waitFor({timeout:7000});
 assert.match(await d.innerText(),new RegExp('UDP '+spec.port+' · subnet'));await d.getByRole('button',{name:'Later',exact:true}).click();assert.match(await p.locator('main').innerText(),new RegExp('UDP '+spec.port));result['port'+spec.port]=true;
}
await p.setViewportSize({width:360,height:800});await p.goto(base+'Panel.dc.html?screen=user&device=phone&lang=en&persist=0');await p.locator('[data-pup="card-body"] img').first().waitFor();await p.waitForTimeout(250);
const qr=await p.locator('[data-pup="card-body"] img').first().boundingBox();assert(qr.y+qr.height<800,JSON.stringify(qr));result.phoneQr={top:Math.round(qr.y),bottom:Math.round(qr.y+qr.height)};
await p.goto(base+'Telegram.dc.html');await p.evaluate(()=>__dcSetProps('Telegram',{view:'miniapp',screen:'settings-access',lang:'en',tgScheme:'night',persist:false}));await p.getByRole('combobox',{name:'Settings section',exact:true}).waitFor();await p.waitForTimeout(250);
const hero=p.locator('[data-sc-name="Panel-settings"] [data-hero]').first();assert.equal(await hero.getAttribute('data-tgc'),'on');assert.equal(await hero.getAttribute('data-theme'),'dark');result.miniSettings={theme:await hero.getAttribute('data-theme'),height:Math.round((await hero.boundingBox()).height)};
await p.evaluate(()=>__dcSetProps('Telegram',{view:'miniapp',screen:'user',lang:'en',tgScheme:'night',persist:false}));await p.locator('[data-pup="card-body"] img').first().waitFor();result.miniUser=true;
result.errors=errors;assert.equal(errors.length,0);fs.writeFileSync(path.join(__dirname,'flow-results.json'),JSON.stringify(result,null,2));console.log(result);await b.close();
})().catch(e=>{console.error(e);process.exit(1)});
