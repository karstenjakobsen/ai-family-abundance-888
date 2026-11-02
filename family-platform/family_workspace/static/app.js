'use strict';
const $ = s => document.querySelector(s);
let user = null, terminal = null, fit = null, socket = null, active = 'terminal', observedListeners=[];
const labels = {terminal:'Agent console',intent:'Requests & goals',jobs:'Jobs',journal:'Journal',host:'Host guardian'};
async function api(path, options={}) {
  const response = await fetch('/api/'+path, {credentials:'same-origin',...options, headers:{'Content-Type':'application/json',...(options.headers||{})}});
  const value = await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== 'login') showLogin();
    const detail = typeof value.detail === 'string' ? value.detail : 'Check the form fields and try again.';
    throw new Error(detail);
  }
  return value;
}
function post(path, data={}) {return api(path,{method:'POST',body:JSON.stringify(data)});}
function notify(message) {$('#notice').textContent=message;$('#notice').hidden=false;}
function element(tag, text, cls) {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(cls)node.className=cls;return node;}
function empty(container, text) {container.replaceChildren(element('div',text,'empty'));}
function when(value) {return new Date(value*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
function showLogin() {user=null;if(socket)socket.close();$('#workspace').hidden=true;$('#login').hidden=false;}
async function enter(member) {
  user=member;$('#login').hidden=true;$('#workspace').hidden=false;
  $('#member-name').textContent=user.name;$('#member-role').textContent=user.role;
  document.querySelectorAll('[data-admin]').forEach(n=>n.hidden=user.role!=='admin');
  document.querySelector('[data-view="terminal"]').hidden=user.role!=='admin';
  await overview();show(user.role==='admin'?'terminal':'intent');
}
async function overview() {
  try {
    const [records,jobs,status]=await Promise.all([api('records'),api('jobs'),api('status')]);
    $('#record-count').textContent=records.length;$('#job-count').textContent=jobs.length;
    $('#guard-mode').textContent=status.guard_mode==='contain'?'Contain':'Observe';
    $('#privilege').textContent=status.process_uid===0?'Service user: root':'Service UID: '+status.process_uid;
    if(status.budget) $('#budget').textContent=`Funded: ${status.budget.funded.toLocaleString()} · Used/reserved: ${status.budget.used_or_reserved.toLocaleString()} · Protected reserve: ${status.budget.reserve.toLocaleString()} · Available to dispatch: ${status.budget.dispatch_available.toLocaleString()}`;
  }catch(error){notify(error.message);}
}
function show(view) {
  if(!user || ((view==='terminal'||view==='host') && user.role!=='admin')) return;
  active=view;$('#current-view').textContent=labels[view];$('#notice').hidden=true;
  document.querySelectorAll('[data-panel]').forEach(n=>n.hidden=n.dataset.panel!==view);
  document.querySelectorAll('[data-view]').forEach(n=>n.classList.toggle('active',n.dataset.view===view));
  if(view==='terminal') {if(!socket || socket.readyState>1) connect();else setTimeout(()=>fit?.fit(),50);}
  if(view==='intent') loadRecords();if(view==='jobs'){loadJobs();overview();}if(view==='journal')loadJournal();if(view==='host')loadHost();
}
function connect() {
  if(!user || user.role!=='admin')return;
  if(socket && socket.readyState<2)return;
  if(!window.Terminal || !window.FitAddon){notify('Terminal library missing. Run tools/vendor.py during installation.');return;}
  if(!terminal){
    terminal=new Terminal({cursorBlink:true,fontSize:13,fontFamily:'ui-monospace, SFMono-Regular, Consolas, monospace',scrollback:3000,theme:{background:'#10150f',foreground:'#c6d8b6',cursor:'#c3dc83',selectionBackground:'#41552f'}});
    fit=new FitAddon.FitAddon();terminal.loadAddon(fit);terminal.open($('#terminal'));
    terminal.onData(data=>{if(socket?.readyState===1)socket.send(JSON.stringify({type:'input',data}));});
    terminal.onResize(size=>{if(socket?.readyState===1)socket.send(JSON.stringify({type:'resize',...size}));});
    new ResizeObserver(()=>{if(active==='terminal')fit.fit();}).observe($('#terminal'));
  }
  terminal.reset();$('#connection-state').textContent='/ connecting';
  socket=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/terminal');
  socket.onopen=()=>{$('#connection-state').textContent='/ connected';setTimeout(()=>{fit.fit();terminal.focus();socket.send(JSON.stringify({type:'resize',cols:terminal.cols,rows:terminal.rows}));},50);};
  socket.onmessage=event=>{const message=JSON.parse(event.data);if(message.type==='output')terminal.write(message.data);if(message.type==='ended'){$('#connection-state').textContent='/ process ended';socket.close();notify('The console process ended. The agent slot is available for approved background jobs.');}};
  socket.onclose=()=>{$('#connection-state').textContent='/ disconnected';};
  socket.onerror=()=>notify('Terminal connection failed. Check your session and the host service.');
}
async function loadRecords(){try{const rows=await api('records?q='+encodeURIComponent($('#search').value));const target=$('#records');target.replaceChildren();if(!rows.length){empty(target,'No matching intentions yet. Start with something your family wants to do.');return;}for(const row of rows){const item=element('article',undefined,'item');const head=element('div',undefined,'item-head');head.append(element('span',row.kind.toUpperCase(),'pill'),element('time',when(row.created)));item.append(head,element('h3',row.title),element('p',row.body));const actions=element('div',undefined,'item-actions');const status=element('select');status.setAttribute('aria-label','Status for '+row.title);for(const value of ['open','active','done','blocked']){const option=element('option',value);option.value=value;option.selected=row.status===value;status.append(option);}status.addEventListener('change',async()=>{try{await post('records/'+row.id+'/status',{status:status.value});}catch(error){notify(error.message);}});actions.append(status);item.append(actions);target.append(item);}}catch(error){notify(error.message);}}
async function loadJobs(){try{const rows=await api('jobs');const target=$('#jobs');target.replaceChildren();if(!rows.length){empty(target,'No jobs yet. A small, well-defined first step is a good place to start.');return;}for(const row of rows){const item=element('article',undefined,'item');const head=element('div',undefined,'item-head');head.append(element('span',row.status==='pending'&&!row.approved?'AWAITING APPROVAL':row.status.replaceAll('_',' ').toUpperCase(),'pill'),element('time',when(row.due)));item.append(head,element('h3',row.title),element('p',row.prompt),element('p','Estimated allowance: '+row.allowance.toLocaleString()+' tokens'));if(user.role==='admin' && row.status==='pending'&&!row.approved){const approve=element('button','Approve this exact job ↗','secondary');approve.addEventListener('click',async()=>{try{await post('jobs/'+row.id+'/approve');await loadJobs();await overview();}catch(error){notify(error.message);}});item.append(approve);}if(user.role==='admin'&&row.status==='outcome_unknown'){const note=element('p','Queue paused. Inspect external effects and reconcile before any further dispatch.');item.append(note);const button=element('button','Reconcile outcome…','secondary');button.addEventListener('click',async()=>{const input=window.prompt('After checking external effects, enter the actual total input + output tokens. Use the reserved allowance if usage remains unknown. This closes the attempt without retrying it.',String(row.actual_tokens??row.allowance));if(input===null)return;const amount=Number(input);if(!Number.isSafeInteger(amount)||amount<0){notify('Enter a nonnegative whole token amount.');return;}try{await post('jobs/'+row.id+'/resolve',{actual_tokens:amount});await loadJobs();await overview();}catch(error){notify(error.message);}});item.append(button);}if(row.result){const details=element('details');details.append(element('summary','Result / checkpoint'),element('pre',row.result));item.append(details);}target.append(item);}}catch(error){notify(error.message);}}
async function loadJournal(){try{const rows=await api('journal');const target=$('#journal');target.replaceChildren();if(!rows.length){empty(target,'The story starts here.');return;}for(const row of rows){const item=element('article',undefined,'journal-entry');const text=element('div');text.append(element('strong',row.action.replaceAll('.',' / ')));let data;try{data=JSON.stringify(JSON.parse(row.data),null,2);}catch{data=row.data;}text.append(element('p',data==='{}'?'Recorded by '+row.actor:data));item.append(element('time',when(row.at)),text);target.append(item);}}catch(error){notify(error.message);}}
async function loadHost(){try{renderHost(await api('host'));}catch(error){notify(error.message);}}
function renderHost(report){observedListeners=report.listeners||[];$('#host-mode').textContent=report.mode==='contain'?'Containment enabled':'Observe & record';const target=$('#findings');target.replaceChildren();for(const finding of report.findings||[])target.append(element('p',finding.code.replaceAll('_',' ')+(finding.address?' · '+finding.address:'')+(finding.detail?' · '+finding.detail:'')));if(!report.findings?.length)target.append(element('p','No findings in the checks performed.'));if(report.ssh_monitor)target.append(element('p','SSH journal monitor: '+report.ssh_monitor));$('#listeners').textContent=observedListeners.join('\n')||'No listener observation available.';}
document.querySelectorAll('[data-view]').forEach(n=>n.addEventListener('click',()=>show(n.dataset.view)));
document.querySelectorAll('[data-go]').forEach(n=>n.addEventListener('click',()=>show(n.dataset.go)));
$('#connect').addEventListener('click',()=>{if(socket)socket.close();socket=null;connect();});
const releaseButton=element('button','Release console for jobs','secondary');releaseButton.id='release-console';releaseButton.dataset.admin='';const consoleControls=element('div',undefined,'console-controls');const connectButton=$('#connect');connectButton.replaceWith(consoleControls);consoleControls.append(connectButton,releaseButton);releaseButton.addEventListener('click',async()=>{if(!window.confirm('End the console process and release its agent slot? Any active console task will be interrupted.'))return;try{await post('terminal/stop');if(socket)socket.close();socket=null;$('#connection-state').textContent='/ slot released';notify('Console stopped. The worker can now dispatch approved jobs within its budget.');}catch(error){notify(error.message);}});
$('#login-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;$('#login-error').textContent='';try{const form=new FormData(event.target);await enter(await post('login',{name:form.get('name'),password:form.get('password')}));event.target.reset();}catch(error){$('#login-error').textContent=error.message;}finally{button.disabled=false;}});
$('#logout').addEventListener('click',async()=>{try{await post('logout');}finally{showLogin();}});
$('#record-form').addEventListener('submit',async event=>{event.preventDefault();try{const form=Object.fromEntries(new FormData(event.target));await post('records',form);event.target.reset();await loadRecords();await overview();notify('Intention saved in durable memory.');}catch(error){notify(error.message);}});
$('#job-form').addEventListener('submit',async event=>{event.preventDefault();try{const form=Object.fromEntries(new FormData(event.target));form.allowance=Number(form.allowance);form.due=form.due?new Date(form.due).toISOString():null;await post('jobs',form);event.target.reset();await loadJobs();await overview();notify('Job proposed. An administrator must approve these exact instructions.');}catch(error){notify(error.message);}});
$('#search').addEventListener('input',loadRecords);$('#refresh-journal').addEventListener('click',loadJournal);
$('#audit').addEventListener('click',async()=>{try{renderHost(await post('host/audit'));}catch(error){notify(error.message);}});
$('#baseline').addEventListener('click',async()=>{if(!window.confirm('Have you inspected the listening TCP sockets and confirmed they are expected?'))return;try{await post('host/baseline',{listeners:observedListeners});await loadHost();notify('Listener baseline approved and recorded.');}catch(error){notify(error.message);}});
api('me').then(enter).catch(showLogin);
