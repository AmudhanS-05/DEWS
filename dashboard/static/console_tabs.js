/* Metrics tab (pipeline: throughput and lag from the live view, latency waterfall and fault drills from PostgreSQL; agents: from
   agent_events) and Agents tab (live flow of agentic_ai/graph.py in the style of the pipeline diagram). Read-only. Live and replay always apart. */
(function(){
"use strict";
const $=id=>document.getElementById(id), esc=t=>String(t??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const KEEP=150, hist={};            // about 5 minutes at one sample per 2 s, kept since this page opened
function push(k,v){ (hist[k]=hist[k]||[]).push(v); if(hist[k].length>KEEP) hist[k].shift(); }
function spark(k,unit){
  const a=hist[k]||[]; if(a.length<2) return '<span class="note">collecting…</span>';
  const w=140,h=26,m=Math.max(...a,1e-9), n=KEEP-1, off=n-(a.length-1), xy=a.map((v,i)=>[((i+off)/n*w),h-2-(v/m)*(h-5)]), last=xy[xy.length-1];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="last ${a.length} samples, peak ${m.toFixed(2)} ${unit}"><title>peak ${m.toFixed(2)} ${unit}</title><path d="M${xy.map(p=>p[0].toFixed(1)+" "+p[1].toFixed(1)).join("L")}" fill="none" stroke="var(--accent)" stroke-width="1.5"/><circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="2.2" fill="var(--accent)"/></svg>`;
}
const age=s=>s==null?"no messages":(s<90?Math.round(s)+" s ago":s<5400?Math.round(s/60)+" min ago":(s/3600).toFixed(1)+" h ago");
const num=n=>Number(n||0).toLocaleString();
window.onMetrics=function(m){
  if(!m) return;
  m.topics.forEach(t=>push("t:"+t.name,t.rate)); m.groups.forEach(g=>push("g:"+g.id,g.lag));
  if($("tab-metrics").hidden) return;
  $("mx-topics").innerHTML=m.topics.map(t=>`<tr><td>${esc(t.name)}</td><td class="r">${t.rate.toFixed(1)}</td><td>${spark("t:"+t.name,"msg/s")}</td><td class="r">${num(t.total)}</td><td>${age(t.age_s)}</td></tr>`).join("")||'<tr><td colspan="5" class="note">no topics seen: is Kafka up?</td></tr>';
  $("mx-groups").innerHTML=m.groups.map(g=>`<tr><td>${esc(g.id)}</td><td class="r">${g.members}</td><td>${esc(g.state)}</td><td class="r">${num(g.lag)}</td><td>${spark("g:"+g.id,"messages behind")}</td></tr>`).join("")||'<tr><td colspan="5" class="note">no consumer groups running</td></tr>';
};
const fmtS=s=>s==null?"":(Math.abs(s)<1?s.toFixed(3)+" s":Math.abs(s)<90?s.toFixed(1)+" s":Math.abs(s)<5400?(s/60).toFixed(1)+" min":Math.abs(s)<172800?(s/3600).toFixed(1)+" h":(s/86400).toFixed(0)+" days");
async function loadMetrics(){
  const box=$("mx-water");
  try{
    const d=await (await fetch("/api/metrics")).json();
    if(!d.ok){ box.innerHTML=`<div class="note">${esc(d.error)}</div>`; return; }
    drawAgentMetrics(d);
    if(!d.waterfall.length){ box.innerHTML='<div class="note">no traces yet: they appear once the forecaster has run</div>'; return; }
    box.innerHTML=d.waterfall.map(w=>{
      const vals=w.stages.filter(s=>s.n&&s.median_s>0).map(s=>s.median_s), top=Math.max(...vals,1), lg=v=>Math.log10(1+Math.max(0,v))/Math.log10(1+top)*100;
      return `<h3>${w.mode==="SIMULATION"?"Replay (SIMULATION)":"Live"} <span class="note">${num(w.traces)} traces${w.older_traces?`, ${num(w.older_traces)} older ones without full timing left out`:""}</span></h3><table><thead><tr><th>Stage</th><th>Median, log scale</th><th class="r">Median</th><th class="r">p95</th><th class="r">Traces measured</th></tr></thead><tbody>`+
        w.stages.map(s=>`<tr><td>${esc(s.label)}</td><td>${s.n?`<span class="wbar" style="width:${lg(s.median_s).toFixed(1)}%"></span>`:`<span class="note">${s.applies?"not measured yet":"does not apply to a replay"}</span>`}</td><td class="r">${fmtS(s.median_s)}</td><td class="r">${fmtS(s.p95_s)}</td><td class="r">${num(s.n)}</td></tr>`).join("")+"</tbody></table>";
    }).join("");
  }catch(e){ box.innerHTML='<div class="note">cannot reach the console server</div>'; }
}
let detRows=[], detKey="";
function drawDetection(det){
  if(!det) return; detRows=det.rows||[];
  const hs=[...new Set(detRows.map(r=>r.horizon_h))].sort((a,b)=>a-b), sel=$("mx-det-h"), k=hs.join();
  if(k!==detKey){ detKey=k; const cur=sel.value||"3"; sel.innerHTML=hs.map(h=>`<option value="${h}">${h} h</option>`).join(""); sel.value=hs.includes(+cur)?cur:(hs[0]||""); }
  const a=det.active, used=(det.used||[]).map(u=>`${u.mode==="SIMULATION"?"replay":"live"}: ${esc(u.model)} at ${u.horizon_h} h`).join(", ");
  $("mx-det-note").innerHTML=`Forecast of crossing an official category, pooled over the gauges, from evaluation_runs. Active model: <b>${a?esc(a.name)+" at "+(a.horizons||[]).join(", ")+" h":"none (rise-rate rule)"}</b>${used?". Last forecast: "+used:""}. Other horizons use the rise-rate rule.`;
  const pct=v=>v==null?"":(+v).toFixed(2), hrs=v=>v==null?"":(+v).toFixed(2)+" h", rows=detRows.filter(r=>String(r.horizon_h)===sel.value);
  $("mx-det").innerHTML=rows.map(r=>`<tr><td>${esc(r.run)}</td><td>${esc(r.data_source)}</td><td>${esc(r.model)}</td><td class="r">${pct(r.precision)}</td><td class="r">${pct(r.recall)}</td><td class="r">${pct(r.f1)}</td><td class="r">${r.false_alarms==null?"":num(r.false_alarms)}</td><td class="r">${hrs(r.lead_gross_h)}</td><td class="r">${hrs(r.lead_net_h)}</td></tr>`).join("")||'<tr><td colspan="9" class="note">no evaluation rows: python -m ml.import_runs</td></tr>';
}
$("mx-det-h").addEventListener("change",()=>loadMetrics());
function drawAgentMetrics(d){
  drawDetection(d.detection);
  const ls=d.langsmith||{};
  $("mx-ls").innerHTML=`Measured from agent_events in PostgreSQL. LangSmith is separate: when tracing is on, it keeps the full trace of each run (prompts, tool calls, tokens) in the cloud; these figures do not depend on it. Tracing is <b>${ls.enabled?"on, project "+esc(ls.project):"off"}</b>${ls.enabled?' · <a href="https://smith.langchain.com" target="_blank" rel="noopener">open LangSmith</a>':""}.`;
  $("mx-drills").innerHTML=(d.drills||[]).map(r=>`<tr><td>${esc(r.scenario)}</td><td class="${r.status==="failed"?"tag-bad":"tag-ok"}">${esc(r.status)}</td><td class="r">${r.recovery_s==null||r.recovery_s<0?"":r.recovery_s.toFixed(1)+" s"}</td><td>${esc(r.data_source)}</td><td>${esc(r.reason||"")}</td><td class="t">${new Date(r.at).toLocaleString([], {hour12:false})}</td></tr>`).join("")||'<tr><td colspan="6" class="note">no drills recorded</td></tr>';
  const rows=d.agents||[];
  $("mx-agents").innerHTML=rows.length?rows.map(m=>{
    const lab=m.mode==="SIMULATION"?"Replay (SIMULATION)":m.mode==="LIVE"?"Live":"Runs with no alert row (seed or test data)";
    const kv=[["Runs",num(m.runs)],["Duration, median",fmtS(m.median_s)],["Duration, p95",fmtS(m.p95_s)],["Reviewer passed",m.judged?`${m.passed} of ${m.judged}`:"not measured yet"],["Review rounds per run",m.avg_review_rounds==null?"":(+m.avg_review_rounds).toFixed(1)],["Revisions",num(m.revisions)],["Disagreements",num(m.disagreements)],["Tool calls",num(m.tool_calls)],["Tokens per run",m.tokens_per_run==null?"":num(m.tokens_per_run)],["v1 to v2, median",m.v1_to_v2_n?fmtS(m.v1_to_v2_s)+` (${m.v1_to_v2_n})`:"not measured yet"]];
    return `<h3>${lab}</h3><div class="mx-two"><table><tbody>${kv.map(([k,v])=>`<tr><td>${k}</td><td class="r">${v}</td></tr>`).join("")}</tbody></table>`+
      `<table><thead><tr><th>Agent</th><th class="r">Time, median per run</th><th class="r">Tokens, median per run</th><th class="r">Runs</th></tr></thead><tbody>${m.per_agent.map(a=>`<tr><td>${esc(a.agent)}</td><td class="r">${fmtS((a.median_ms||0)/1000)}</td><td class="r">${a.median_tokens?num(a.median_tokens):""}</td><td class="r">${a.runs}</td></tr>`).join("")}</tbody></table></div>`;
  }).join("")+'<div class="note" style="margin-top:6px">v1 to v2 is long while the agents are started by hand after the alert; it means more once the agent consumer runs on its own.</div>':'<div class="note">No agent run recorded yet.</div>';
}
/* the graph in agentic_ai/graph.py, drawn like the pipeline: same nodes, status dots and moving dashes */
const AL={alert:["topic",16,170,132,64,"alerts.flood"],coordinator:["group",176,170,132,64,"Coordinator"],evidence:["group",336,50,132,64,"Evidence"],tools:["info",336,170,132,64,"Tools (MCP)"],context:["group",336,290,132,64,"Context"],
  reconcile:["info",496,170,132,64,"Reconcile"],writer:["group",656,170,132,64,"Writer"],reviewer:["group",816,170,132,64,"Reviewer"],save:["store",976,90,132,64,"Save advisory"],keep:["store",976,250,132,64,"Keep v1"],
  human:["manual",816,390,132,64,"Human gate"],approved:["topic",656,390,132,64,"alerts.approved"],notifier:["group",496,390,132,64,"Notifier"]};
const AE=[["e_a_c","alert","coordinator","M148 202H176"],["e_c_e","coordinator","evidence","M308 190C322 190 318 82 336 82"],["e_c_x","coordinator","context","M308 214C322 214 318 322 336 322"],
  ["e_e_t","evidence","tools","M402 114V170"],["e_x_t","context","tools","M402 290V234"],["e_e_r","evidence","reconcile","M468 82C482 82 478 190 496 190"],["e_x_r","context","reconcile","M468 322C482 322 478 214 496 214"],
  ["e_r_w","reconcile","writer","M628 202H656"],["e_w_v","writer","reviewer","M788 202H816"],["e_v_s","reviewer","save","M948 190C962 190 958 122 976 122"],["e_v_k","reviewer","keep","M948 214C962 214 958 282 976 282"],
  ["e_s_h","save","human","M1042 154V170H1108V422H948"],["e_k_h","keep","human","M1042 314V422H948"],["e_h_a","human","approved","M816 422H788"],["e_a_n","approved","notifier","M656 422H628"]];
function agentState(d){
  const by={}; (d.agents||[]).forEach(a=>by[a.agent]=a); const act=!!d.active, st={};
  const set=(id,status,lines)=>{ st[id]={status,lines:lines||[]}; };
  const agentNode=id=>{ const a=by[id]; if(!a){ set(id,"absent",[d.run?"not reached":"waiting for a run"]); return; }
    const running=act&&a.open, bits=[]; if(a.ms) bits.push((a.ms/1000).toFixed(1)+" s"); if(a.tokens) bits.push(num(a.tokens)+" tokens");
    const l2=[]; if(a.starts>1) l2.push(a.starts+" tries"); if(a.tool_calls) l2.push(a.tool_calls+" tool calls"); if(a.verdict) l2.push(a.verdict);
    set(id,running?"run":(a.verdict&&a.verdict!=="pass"?"warn":"ok"),[running?"running…":bits.join(" · ")||"ran",l2.join(" · ")]); };
  ["coordinator","evidence","context","writer","reviewer"].forEach(agentNode);
  set("alert",d.run?"ok":"absent",d.run?[String(d.run.alert_id).split("|").slice(0,2).join(" "),d.run.mode||""]:["no alert yet"]);
  const calls=(d.agents||[]).reduce((n,a)=>n+(a.tool_calls||0),0); set("tools",calls?"ok":"absent",[calls?num(calls)+" calls this run":"no calls"]);
  const merged=st.evidence.status==="ok"&&st.context.status==="ok"; set("reconcile",merged?"ok":"absent",[merged?"evidence and context":"not reached"]);
  const adv=d.advisory, v2=adv&&adv.version>1, rvDone=st.reviewer.status!=="absent"&&st.reviewer.status!=="run";
  set("save",v2?"ok":"absent",[v2?`v${adv.version} saved`:"not reached"]); set("keep",rvDone&&!v2?"warn":"absent",[rvDone&&!v2?"template stays":"not reached"]);
  const s=adv&&adv.status;
  set("human",s==="draft"||s==="rejected"?"warn":(s==="approved"||s==="sent")?"ok":"absent",[s==="draft"?"waiting for a person":(s==="approved"||s==="sent")?"approved":s?esc(s):"not reached"]);
  set("approved",s==="approved"||s==="sent"?"ok":"absent",[s==="approved"||s==="sent"?"published":"needs approval"]);
  set("notifier",s==="sent"?"ok":"absent",[s==="sent"?"delivered":"not reached"]);
  return st;
}
function edgeClass(st,from,to){ const a=st[from].status, b=st[to].status, seen=x=>x!=="absent"; if(b==="run") return "flow"; if(seen(a)&&b==="warn"&&to==="human") return "flow"; return seen(a)&&seen(b)?"idle":"inactive"; }
function agentGraph(d){
  const st=agentState(d);
  const edges=AE.map(([id,f,t,p])=>`<path d="${p}" class="edge ${edgeClass(st,f,t)}" marker-end="url(#agarr)"/>`).join("");
  const rev=d.revisions||0, back=`<path d="M880 170C880 132 724 132 724 170" class="edge ${rev?(d.active?"flow":"idle"):"inactive"}" marker-end="url(#agarr)"/><text x="802" y="130" text-anchor="middle" class="elabel">revise${rev?" ×"+rev:""}</text>`;
  const nodes=Object.entries(AL).map(([id,[kind,x,y,w,h,title]])=>{ const s=st[id], L=s.lines; return `<g class="node ${kind} ${s.status}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/><circle cx="${x+w-14}" cy="${y+14}" r="5" class="sdot ${s.status}"/><text x="${x+10}" y="${y+22}" class="ttl">${esc(title)}</text>${L.map((l,i)=>l?`<text x="${x+10}" y="${y+38+i*14}" class="ln">${esc(String(l).slice(0,24))}</text>`:"").join("")}</g>`; }).join("");
  return `<svg viewBox="0 0 1120 470" role="img" aria-label="Live agent flow"><defs><marker id="agarr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#52657a"/></marker></defs>${edges}${back}${nodes}</svg>`;
}
const setHtml=(id,h)=>{ const e=$(id); if(e.dataset.h!==h){ e.innerHTML=h; e.dataset.h=h; } };     // an unchanged graph keeps its dashes moving
/* step through a recorded run: the graph as it stood after event i (same counting as agent_run_view in flow_monitor.py) */
function agentsAt(events,i){
  const by={}; let revisions=0, disagreements=0;
  for(const e of events.slice(0,i+1)){ const a=by[e.agent]=by[e.agent]||{agent:e.agent,starts:0,ends:0,ms:0,tool_calls:0,tokens:0,verdict:null}, x=e.detail||{};
    if(e.kind==="start") a.starts++; else if(e.kind==="end"){ a.ends++; a.ms+=x.ms||0; a.tokens+=x.tokens||0; } else if(e.kind==="tool_call") a.tool_calls++; else if(e.kind==="verdict") a.verdict=x.verdict; else if(e.kind==="revision") revisions++; else if(e.kind==="disagreement") disagreements++; }
  const agents=Object.values(by); agents.forEach(a=>a.open=a.starts>a.ends);
  return {agents,revisions,disagreements};
}
let agLast=null, agPick="", agFollow=true;
function drawAgentStep(){
  const d=agLast; if(!d||!d.run) return; const ev=d.events||[], rg=$("ag-t");
  rg.max=Math.max(0,ev.length-1); if(agFollow) rg.value=rg.max;
  const i=+rg.value, end=i>=ev.length-1;
  $("ag-t-l").textContent=ev.length?`step ${i+1} of ${ev.length} · ${new Date(ev[i].ts).toLocaleTimeString([], {hour12:false})} · ${ev[i].agent} ${ev[i].kind}`:"";
  setHtml("ag-graph",agentGraph(end?d:{...d,...agentsAt(ev,i),active:true,advisory:null}));
}
$("ag-t").addEventListener("input",()=>{ agFollow=+$("ag-t").value>=+$("ag-t").max; drawAgentStep(); });
$("ag-run").addEventListener("change",e=>{ agPick=e.target.value; agFollow=true; loadAgents(); });
async function loadAgents(){
  try{
    const d=await (await fetch("/api/agents"+(agPick?"?run="+encodeURIComponent(agPick):""))).json(); agLast=d;
    const sel=$("ag-run"), opts=`<option value="">newest run (follows live)</option>`+(d.runs||[]).map(r=>`<option value="${esc(r.run_id)}">${new Date(r.ended_at).toLocaleString([], {hour12:false})} · ${esc(String(r.alert_id).split("|").slice(0,2).join(" "))}</option>`).join("");
    if(sel.dataset.h!==opts){ sel.innerHTML=opts; sel.dataset.h=opts; sel.value=agPick; }
    if(!d.ok){ $("ag-note").textContent=d.error; setHtml("ag-graph",agentGraph({})); return; }
    const ls=d.langsmith&&d.langsmith.enabled?` · LangSmith traces in project <b>${esc(d.langsmith.project)}</b> (<a href="https://smith.langchain.com" target="_blank" rel="noopener">open</a>)`:" · LangSmith tracing off";
    if(!d.run){ $("ag-note").innerHTML="No agent run recorded yet. A run appears after python -m agentic_ai.run --alert-id &lt;id&gt;."+ls; setHtml("ag-graph",agentGraph({})); $("ag-agents").innerHTML=""; $("ag-events").innerHTML=""; return; }
    const r=d.run;
    $("ag-note").innerHTML=`${d.active?"<b>Running now</b>":"Last run"} <b>${esc(r.run_id)}</b> for alert <b>${esc(r.alert_id)}</b>${r.mode?" · <b>"+esc(r.mode)+"</b>":""} · ${r.duration_s==null?"":r.duration_s.toFixed(1)+" s"} · ${r.review_rounds} review round${r.review_rounds===1?"":"s"} · last verdict ${esc(r.last_verdict||"none")} · ${d.disagreements} disagreement${d.disagreements===1?"":"s"} · ${new Date(r.ended_at).toLocaleString([], {hour12:false})}${ls}`;
    drawAgentStep();
    $("ag-agents").innerHTML=d.agents.map(a=>`<tr><td>${esc(a.agent)}</td><td class="r">${a.starts}</td><td class="r">${a.ms?(a.ms/1000).toFixed(1)+" s":""}</td><td class="r">${a.tool_calls||""}</td><td class="r">${a.tokens?num(a.tokens):""}</td><td>${esc(a.verdict||"")}</td></tr>`).join("");
    $("ag-events").innerHTML=d.events.slice().reverse().map(e=>{ const dt=e.detail||{}, txt=dt.name||dt.tool||(dt.reasons&&dt.reasons.length?dt.reasons.join("; "):"")||dt.verdict||""; return `<tr><td class="t">${new Date(e.ts).toLocaleTimeString([], {hour12:false})}</td><td>${esc(e.agent)}</td><td>${esc(e.kind)}</td><td>${esc(String(txt).slice(0,140))}</td></tr>`; }).join("");
  }catch(e){ $("ag-note").textContent="cannot reach the console server"; }
}
window.onTabShown=t=>{ if(t==="metrics"){ loadMetrics(); if(window.lastView) window.onMetrics(window.lastView.metrics); } if(t==="agents") loadAgents(); };
setInterval(()=>{ if(!$("tab-metrics").hidden) loadMetrics(); },8000);
setInterval(()=>{ if(!$("tab-agents").hidden) loadAgents(); },2500);
})();
