const $ = s => document.querySelector(s);
let spaces = [], current = 'office', activeTask = null, timer;
const el = (tag, text, cls) => { const n = document.createElement(tag); if(text !== undefined) n.textContent = text; if(cls) n.className = cls; return n; };
async function api(path, options = {}) { const response = await fetch('/api' + path, {headers: {'Content-Type':'application/json'}, ...options}); const body = await response.json(); if(!response.ok) throw Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)); return body; }
function showNotice(text){ $('#notice').textContent = text; }
function select(id){ current = id; activeTask = null; clearTimeout(timer); $('#home').hidden = false; $('#task-view').hidden = true; showNotice(''); render(); refreshTasks(); }
function render(){
  const space = spaces.find(s => s.id === current);
  $('#current-name').textContent = space.name; $('#workspace-chip').textContent = space.icon + ' ' + space.name;
  $('#workspaces').replaceChildren(); $('#cards').replaceChildren();
  spaces.forEach(s => {
    const n = el('button', undefined, s.id === current ? 'active' : ''); n.append(el('span',s.icon),el('span',s.name),el('span',s.id === current ? '⌑' : '')); n.onclick = () => select(s.id); $('#workspaces').append(n);
    const card = el('button',undefined, 'space-card ' + s.color + (s.id === current ? ' selected' : ''));
    card.append(el('span',s.icon,'space-icon'),el('small',s.id === current ? '当前空间' : '↗'),el('strong',s.name),el('p',s.description)); card.onclick = () => select(s.id); $('#cards').append(card);
  });
  document.querySelectorAll('.suggestions button').forEach(b => b.hidden = current !== 'office');
}
async function refreshTasks(){ try { const tasks = await api('/tasks?workspace_id=' + encodeURIComponent(current)); $('#recent').replaceChildren(); renderFeed(tasks); tasks.slice(0,12).forEach(t => { const b = el('button', (t.status === 'completed' ? '✓ ' : t.status === 'failed' ? '× ' : '◌ ') + t.prompt); b.onclick = () => openTask(t.id); $('#recent').append(b); }); } catch(e){ showNotice(e.message); } }
async function openTask(id){ activeTask = id; $('#home').hidden = true; $('#task-view').hidden = false; await pollTask(id); }
async function pollTask(id){
  clearTimeout(timer);
  try {
    const task = await api('/tasks/' + id); if(activeTask !== id) return;
    $('#task-title').textContent = task.prompt; const states = {queued:'排队中',running:'Agent 正在工作',completed:'已完成',failed:'执行失败'};
    $('#task-meta').textContent = `${states[task.status] || task.status} · ${task.model_id || 'Deep Agent'} ${task.reasoning_effort || '默认'} · 配置 v${task.revision} · ${new Date(task.created_at).toLocaleString()}`;
    $('#task-output').textContent = task.error || task.output || '正在调用当前工作空间的 Agent，请稍候…';
    $('#artifacts').replaceChildren(); task.artifacts.forEach(name => { const link = el('a','↓ ' + name); link.href = '/api/tasks/' + id + '/artifacts/' + encodeURIComponent(name); link.download = name; $('#artifacts').append(link); });
    if(['running','queued'].includes(task.status)) timer = setTimeout(() => pollTask(id),1500); else refreshTasks();
  } catch(e){ $('#task-output').textContent = e.message; }
}
$('#send').onclick = async () => { const prompt = $('#prompt').value.trim(); if(!prompt) { $('#prompt').focus(); return; } $('#send').disabled = true; showNotice(''); try { const task = await api('/workspaces/' + current + '/tasks',{method:'POST',body:JSON.stringify({prompt,model_id:$('#model-select').value || null,reasoning_effort:$('#effort-select').value || null})}); $('#prompt').value = ''; openTask(task.id); } catch(e){ showNotice(e.message); } finally { $('#send').disabled = false; } };
$('#new-task').onclick = () => { select(current); $('#prompt').focus(); };
$('#back').onclick = () => select(current);
document.querySelectorAll('[data-prompt]').forEach(b => b.onclick = () => { $('#prompt').value = b.dataset.prompt; $('#prompt').focus(); });
$('#prompt').onkeydown = e => { if(e.key === 'Enter' && (e.metaKey || e.ctrlKey)){ e.preventDefault(); $('#send').click(); } };
document.addEventListener('keydown', e => { if((e.metaKey || e.ctrlKey) && e.key === 'k'){ e.preventDefault(); $('#new-task').click(); } });
$('#settings').onclick = () => { const s = spaces.find(s => s.id === current); $('#edit-name').value=s.name; $('#edit-prompt').value=s.system_prompt; $('#edit-rules').value=s.rules.join('\n'); $('#edit-skills').value=JSON.stringify(s.skills,null,2); $('#edit-tools').value=JSON.stringify(s.tools,null,2); $('#revision').textContent='配置 v'+s.revision; $('#config-error').textContent=''; $('#config').showModal(); };
$('#close-config').onclick = () => $('#config').close();
$('#config-form').onsubmit = async e => { e.preventDefault(); try { const s = spaces.find(s => s.id === current); const updated = await api('/workspaces/' + current,{method:'PUT',body:JSON.stringify({...s,name:$('#edit-name').value,system_prompt:$('#edit-prompt').value,rules:$('#edit-rules').value.split('\n').filter(Boolean),skills:JSON.parse($('#edit-skills').value),tools:JSON.parse($('#edit-tools').value)})}); spaces = spaces.map(x => x.id === current ? updated : x); render(); $('#config').close(); } catch(e){ $('#config-error').textContent=e.message; } };
$('#add-workspace').onclick = async () => { const name = window.prompt('新工作空间名称'); if(!name?.trim()) return; try { const s = await api('/workspaces',{method:'POST',body:JSON.stringify({name:name.trim()})}); spaces.push(s); select(s.id); } catch(e){ showNotice(e.message); } };
(async () => { try { spaces = await api('/workspaces'); current=spaces[0].id; render(); refreshTasks(); await loadModels(); } catch(e){ showNotice(e.message); } })();

let modelConfig, draftLLM;
function fillSelect(node, items, selected){ node.replaceChildren(); items.forEach(([value,label]) => {const option=el('option',label); option.value=value; node.append(option);}); if(items.some(x=>x[0]===selected))node.value=selected; }
async function loadModels(){ modelConfig=await api('/models'); const previous=$('#model-select').value; fillSelect($('#model-select'),modelConfig.profiles.map(p=>[p.id,p.id]),previous || modelConfig.llm.default); if(!modelConfig.profiles.length)fillSelect($('#model-select'),[['','＋ 新增模型']], ''); renderEfforts(); $('#model-state').textContent=modelConfig.profiles.length?'':'待配置'; }
function renderEfforts(){const profile=modelConfig?.profiles.find(p=>p.id===$('#model-select').value); const previous=$('#effort-select').value; fillSelect($('#effort-select'),[['','默认强度'],...(profile?.reasoning_efforts || []).map(e=>[e,e])],previous);}
$('#model-select').onchange=renderEfforts;
function syncDraft(){ const ids=[]; Object.entries(draftLLM.models).forEach(([source,g])=>Object.keys(g.models).forEach(name=>ids.push([source+'/'+name,source+'/'+name]))); fillSelect($('#default-model'),ids,draftLLM.default); $('#model-json').value=JSON.stringify(draftLLM,null,2); }
$('#manage-models').onclick=async()=>{try{await loadModels();draftLLM=structuredClone(modelConfig.llm);syncDraft();$('#models-error').textContent='';$('#model-key').value='';$('#models-dialog').showModal();}catch(e){showNotice(e.message);}};
$('#close-models').onclick=()=>$('#models-dialog').close();
$('#add-model').onclick=()=>{try{
 const source=$('#source-name').value.trim(), name=$('#model-name').value.trim(), url=$('#model-url').value.trim(), key=$('#model-key').value.trim();
 if(!source || !name || source.includes('/') || name.includes('/'))throw Error('填写不包含斜杠的来源名称与模型 ID。');
 const existing=draftLLM.models[source]; if(!existing && (!url || !key))throw Error('新来源需要 Base URL 和 API Key（或环境变量引用）。');
 const group=existing || {base_url:url,api_key:key,api:$('#model-api').value,models:{}};
 if(group.models[name])throw Error('该模型已存在，可在高级配置中编辑。');
 if(url)group.base_url=url; if(key)group.api_key=key;
 group.models[name]={reasoning_efforts:$('#model-efforts').value.split(',').map(x=>x.trim()).filter(Boolean)};
 draftLLM.models[source]=group; if(!draftLLM.default)draftLLM.default=source+'/'+name;syncDraft();$('#model-key').value='';$('#models-error').textContent='已添加到草稿，请保存模型配置。';
 }catch(e){$('#models-error').textContent=e.message;}};
$('#default-model').onchange=()=>{draftLLM.default=$('#default-model').value;$('#model-json').value=JSON.stringify(draftLLM,null,2);};
$('#apply-model-json').onclick=()=>{try{const value=JSON.parse($('#model-json').value);if(!value.models || typeof value.models!=='object')throw Error('需要 models 来源配置。');draftLLM=value;syncDraft();$('#models-error').textContent='高级配置已应用，请保存。';}catch(e){$('#models-error').textContent=e.message;}};
$('#models-form').onsubmit=async e=>{e.preventDefault();try{const config=JSON.parse($('#model-json').value);await api('/models',{method:'PUT',body:JSON.stringify({llm:config,revision:modelConfig.revision})});await loadModels();$('#models-dialog').close();showNotice('模型配置已保存。');}catch(e){$('#models-error').textContent=e.message;}};
function renderFeed(tasks){const feed=$('#task-feed');feed.replaceChildren();if(!tasks.length){feed.append(el('p','还没有任务。选择工作空间和模型，开始新的工作。','feed-empty'));return;}tasks.slice(0,8).forEach(task=>{const button=el('button',undefined,'feed-task');button.append(el('span',task.status==='completed'?'✓':'◌','feed-icon'));const content=el('div');content.append(el('strong',task.prompt),el('small',(task.model_id || 'Deep Agent')+' · '+new Date(task.created_at).toLocaleDateString()));button.append(content);button.onclick=()=>openTask(task.id);feed.append(button);});}
