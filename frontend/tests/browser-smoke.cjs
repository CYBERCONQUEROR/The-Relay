const { chromium } = require(process.env.RELAY_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.RELAY_CHROMIUM_PATH || '/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1512,height:982}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const folder=require('path').resolve(__dirname,'../../docs/screenshots');fs.mkdirSync(folder,{recursive:true});
 await page.goto(process.env.RELAY_QA_URL || 'http://127.0.0.1:8010');
 await page.locator('.environment').filter({hasText:'Backend connected'}).waitFor();
 async function shot(name){
  await page.evaluate(()=>{
   let note=document.getElementById('qa-label');
   if(!note){note=document.createElement('div');note.id='qa-label';note.textContent='OFFLINE QA · MOCKED GROQ RESPONSES';note.style.cssText='position:fixed;bottom:8px;right:10px;z-index:999;background:#412c16;color:#ffe2ad;font:10px monospace;padding:6px 10px;border:1px solid #795c34;border-radius:4px';document.body.appendChild(note)}
  });
  await page.screenshot({path:folder+'/'+name+'.png',fullPage:true});
 }
 await shot('workspace-desktop');
 await page.getByLabel('Research question').fill('How does multi-head attention work?');
 await page.getByLabel('Send question').click();
 await page.locator('.status-message').filter({hasText:"daily token quota appears exhausted"}).waitFor({timeout:20000});
 await shot('quota-wait-desktop');
 await page.getByText('OFFLINE TEST: Multi-head attention combines parallel attention projections [attention_is_all_you_need_v7.pdf, p. 5].',{exact:true}).waitFor({timeout:20000});
 await page.getByText('p. 5',{exact:true}).waitFor();
 await shot('completed-request-desktop');
 await page.getByRole('button',{name:/Queue management/}).first().click();
 await page.getByRole('heading',{name:'Queue management'}).waitFor();
 await shot('queue-desktop');
 await page.getByRole('button',{name:'Live monitoring',exact:true}).click();
 await page.getByRole('heading',{name:'Live monitoring'}).waitFor();
 await shot('monitoring-desktop');
 await page.getByRole('button',{name:'Load testing',exact:true}).click();
 await page.getByLabel('Provider').selectOption('groq');
 await page.getByText('Review & run',{exact:true}).click();
 if(!await page.getByRole('button',{name:'Confirm & start'}).isDisabled())throw Error('Groq test must require checkbox');
 await shot('load-test-confirmation-desktop');
 await page.getByRole('button',{name:'Cancel',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 await page.getByLabel('Toggle navigation').click();
 await page.getByRole('button',{name:'New conversation'}).click();
 await shot('workspace-mobile');
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
 if(overflow)throw Error('Mobile horizontal overflow');
 if(errors.length)throw Error(errors.join('\n'));
 console.log(JSON.stringify({desktop:'passed',mobile:'passed',requestCompletion:'passed',citations:'passed',groqConfirmation:'passed',pageErrors:errors,screenshots:fs.readdirSync(folder)}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
