/* Metrics tab v1 (throughput and lag from the live view, latency waterfall from PostgreSQL) and Agents tab v1 (the graph in
   agentic_ai/graph.py, with the last run from agent_events). Read-only. Live and replay latency are always shown apart. */
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
    if(!d.waterfall.length){ box.innerHTML='<div class="note">no traces yet: they appear once the forecaster has run</div>'; return; }
    box.innerHTML=d.waterfall.map(w=>{
      const vals=w.stages.filter(s=>s.n&&s.median_s>0).map(s=>s.median_s), top=Math.max(...vals,1), lg=v=>Math.log10(1+Math.max(0,v))/Math.log10(1+top)*100;
      return `<h3>${w.mode==="SIMULATION"?"Replay (SIMULATION)":"Live"} <span class="note">${num(w.traces)} traces</span></h3><table><thead><tr><th>Stage</th><th>Median, log scale</th><th class="r">Median</th><th class="r">p95</th><th class="r">Traces measured</th></tr></thead><tbody>`+
        w.stages.map(s=>`<tr><td>${esc(s.label)}</td><td>${s.n?`<span class="wbar" style="width:${lg(s.median_s).toFixed(1)}%"></span>`:'<span class="note">not measured yet</span>'}</td><td class="r">${fmtS(s.median_s)}</td><td class="r">${fmtS(s.p95_s)}</td><td class="r">${num(s.n)}</td></tr>`).join("")+"</tbody></table>";
    }).join("");
  }catch(e){ box.innerHTML='<div class="note">cannot reach the console server</div>'; }
}
/* static graph, as built in agentic_ai/graph.py */
const NODES={coordinator:[20,70],evidence:[190,20],context:[190,120],reconcile:[360,70],writer:[530,70],reviewer:[700,70],save_advisory:[870,20],keep_v1:[870,120]};
const EDGES=[["coordinator","evidence"],["coordinator","context"],["evidence","reconcile"],["context","reconcile"],["reconcile","writer"],["writer","reviewer"],["reviewer","save_advisory"],["reviewer","keep_v1"]];
const NW=130,NH=44;
function graph(seen){
  const mid=n=>[NODES[n][0],NODES[n][1]+NH/2];
  const e=EDGES.map(([a,b])=>{ const x0=NODES[a][0]+NW,y0=NODES[a][1]+NH/2,[x1,y1]=mid(b),c=(x0+x1)/2; return `<path d="M${x0} ${y0}C${c} ${y0} ${c} ${y1} ${x1} ${y1}" class="agedge" marker-end="url(#agarr)"/>`; }).join("");
  const back=`<path d="M${NODES.reviewer[0]+NW/2} ${NODES.reviewer[1]} C${NODES.reviewer[0]+40} 16 ${NODES.writer[0]+90} 16 ${NODES.writer[0]+NW/2} ${NODES.writer[1]}" class="agedge back" marker-end="url(#agarr)"/><text x="${(NODES.reviewer[0]+NODES.writer[0]+NW)/2}" y="24" class="elabel" text-anchor="middle">revise</text>`;
  const n=Object.entries(NODES).map(([k,[x,y]])=>{ const s=seen[k]; return `<g class="agnode${s?" seen":""}"><rect x="${x}" y="${y}" width="${NW}" height="${NH}" rx="8"/><text x="${x+NW/2}" y="${y+(s?19:27)}" text-anchor="middle" class="ttl">${k.replace("_"," ")}</text>${s?`<text x="${x+NW/2}" y="${y+34}" text-anchor="middle" class="ln">${esc(s)}</text>`:""}</g>`; }).join("");
  return `<svg viewBox="0 0 1020 180" role="img" aria-label="Agent graph"><defs><marker id="agarr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--muted)"/></marker></defs>${e}${back}${n}</svg>`;
}
async function loadAgents(){
  try{
    const d=await (await fetch("/api/agents")).json();
    if(!d.ok){ $("ag-note").textContent=d.error; $("ag-graph").innerHTML=graph({}); return; }
    if(!d.run){ $("ag-note").textContent="No agent run recorded yet. This shows the graph; a run appears after python -m agentic_ai.run --alert-id <id>."; $("ag-graph").innerHTML=graph({}); $("ag-agents").innerHTML=""; $("ag-events").innerHTML=""; return; }
    const r=d.run, seen={}; d.agents.forEach(a=>{ if(a.agent in NODES){ const bits=[]; if(a.ms) bits.push((a.ms/1000).toFixed(1)+" s"); if(a.starts>1) bits.push(a.starts+" tries"); if(a.verdict) bits.push(a.verdict); seen[a.agent]=bits.join(" · ")||"ran"; } });
    $("ag-note").innerHTML=`Last run <b>${esc(r.run_id)}</b> for alert <b>${esc(r.alert_id)}</b>${r.mode?" · <b>"+esc(r.mode)+"</b>":""} · ${r.duration_s==null?"":r.duration_s.toFixed(1)+" s"} · ${r.review_rounds} review round${r.review_rounds===1?"":"s"} · last verdict ${esc(r.last_verdict||"none")} · ${d.disagreements} disagreement${d.disagreements===1?"":"s"} · ${new Date(r.ended_at).toLocaleString([], {hour12:false})}`;
    $("ag-graph").innerHTML=graph(seen);
    $("ag-agents").innerHTML=d.agents.map(a=>`<tr><td>${esc(a.agent)}</td><td class="r">${a.starts}</td><td class="r">${a.ms?(a.ms/1000).toFixed(1)+" s":""}</td><td class="r">${a.tool_calls||""}</td><td class="r">${a.tokens?num(a.tokens):""}</td><td>${esc(a.verdict||"")}</td></tr>`).join("");
    $("ag-events").innerHTML=d.events.slice().reverse().map(e=>{ const dt=e.detail||{}, txt=dt.name||dt.tool||(dt.reasons&&dt.reasons.length?dt.reasons.join("; "):"")||dt.verdict||""; return `<tr><td class="t">${new Date(e.ts).toLocaleTimeString([], {hour12:false})}</td><td>${esc(e.agent)}</td><td>${esc(e.kind)}</td><td>${esc(String(txt).slice(0,140))}</td></tr>`; }).join("");
  }catch(e){ $("ag-note").textContent="cannot reach the console server"; }
}
window.onTabShown=t=>{ if(t==="metrics"){ loadMetrics(); if(window.lastView) window.onMetrics(window.lastView.metrics); } if(t==="agents") loadAgents(); };
setInterval(()=>{ if(!$("tab-metrics").hidden) loadMetrics(); if(!$("tab-agents").hidden) loadAgents(); },8000);
})();
