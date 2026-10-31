/* Orbit view: a god's-eye 3D picture of every gauge, drawn on a 2D canvas with a small perspective camera (no libraries).
   Ground = flat plate with a lat/lon graticule (not terrain: no elevation data is drawn). Each gauge is a pillar whose height
   is its newest stage relative to ITS OWN official NWS "major" level, with ticks at the official action/minor/moderate/major
   stages. A radar sweep pings gauges as it passes. Data: /api/gauges (stage, status, levels), /api/advisories (alert beacons). */
(function(){
"use strict";
const SC={normal:"#34d6b0",action:"#e6c04a",minor:"#ff8a3d",moderate:"#ff4d4d",major:"#c084fc",stale:"#5b6b80",nodata:"#46586d"};
const SEV={major:5,moderate:4,minor:3,action:2,normal:1,stale:0,nodata:0};
const CATS=["action","minor","moderate","major"];
const TAU=Math.PI*2;
const root=document.getElementById("orbit"), cv=document.getElementById("o-canvas"), ctx=cv.getContext("2d");
const card=document.getElementById("o-card"), list=document.getElementById("o-list"), tip=document.getElementById("o-tip");
const hudMode=document.getElementById("o-mode"), hudClock=document.getElementById("o-clock"), hudStat=document.getElementById("o-stat");
let W=0,H=0,dpr=1,running=false,raf=0,lastT=performance.now();
const cam={yaw:-0.55,pitch:0.62,dist:150,auto:true};
let G=[],mode="live",selected=null,hover=null,alerts={},hits=[],geo=null,note="",pollA=0,pollB=0;
const stars=Array.from({length:240},()=>({x:Math.random(),y:Math.random(),r:Math.random()*1.2+.2,p:Math.random()*TAU,s:Math.random()*.6+.2}));

/* ---------------------------------------------------------------- geometry */
function buildGeo(gs){
  const lat0=gs.reduce((a,g)=>a+g.lat,0)/gs.length, lon0=gs.reduce((a,g)=>a+g.lon,0)/gs.length, k=111.32*Math.cos(lat0*Math.PI/180);
  gs.forEach(g=>{ g.x=(g.lon-lon0)*k; g.z=(g.lat-lat0)*110.57; });
  const xs=gs.map(g=>g.x), zs=gs.map(g=>g.z);
  const minx=Math.min(...xs),maxx=Math.max(...xs),minz=Math.min(...zs),maxz=Math.max(...zs);
  const span=Math.max(maxx-minx,maxz-minz,40), m=span*.18;
  geo={lat0,lon0,k,span,minx:minx-m,maxx:maxx+m,minz:minz-m,maxz:maxz+m,cx:(minx+maxx)/2,cz:(minz+maxz)/2,hmax:span*.3};
  cam.dist=span*1.55;
}
const toLon=x=>geo.lon0+x/geo.k, toLat=z=>geo.lat0+z/110.57;
function levelRef(g){ const c=g.cats||{}; return c.major||c.moderate||(c.minor?c.minor*1.4:null)||(c.action?c.action*1.8:20); }
const hOf=(g,s)=>Math.max(0,Math.min(1.35,s/levelRef(g)))*geo.hmax;

/* camera */
let B=null;
function basis(){
  const cp=Math.cos(cam.pitch),sp=Math.sin(cam.pitch),cy=Math.cos(cam.yaw),sy=Math.sin(cam.yaw);
  B={pos:[geo.cx+cam.dist*cp*sy,cam.dist*sp,geo.cz+cam.dist*cp*cy],f:[-cp*sy,-sp,-cp*cy],r:[cy,0,-sy],u:[-sy*sp,cp,-cy*sp],F:Math.max(W,H)*.9,cp,sp};
}
function P(x,y,z){
  const d0=x-B.pos[0],d1=y-B.pos[1],d2=z-B.pos[2];
  const zv=d0*B.f[0]+d1*B.f[1]+d2*B.f[2]; if(zv<1) return null;
  return [W/2+(d0*B.r[0]+d1*B.r[1]+d2*B.r[2])/zv*B.F, H/2-(d0*B.u[0]+d1*B.u[1]+d2*B.u[2])/zv*B.F, zv];
}
function line(a,b,style,w){ const p=P(...a),q=P(...b); if(!p||!q) return; ctx.strokeStyle=style; ctx.lineWidth=w||1; ctx.beginPath(); ctx.moveTo(p[0],p[1]); ctx.lineTo(q[0],q[1]); ctx.stroke(); }
function ring(x,y,z,r,n){ const pts=[]; for(let i=0;i<=n;i++){ const p=P(x+Math.cos(i/n*TAU)*r,y,z+Math.sin(i/n*TAU)*r); if(!p) return null; pts.push(p); } return pts; }
function strokePts(pts,style,w){ if(!pts) return; ctx.strokeStyle=style; ctx.lineWidth=w; ctx.beginPath(); pts.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1])); ctx.stroke(); }
const rgba=(hex,a)=>{ const n=parseInt(hex.slice(1),16); return `rgba(${n>>16},${(n>>8)&255},${n&255},${a})`; };

/* ---------------------------------------------------------------- scene */
function drawSky(t){
  const g=ctx.createLinearGradient(0,0,0,H); g.addColorStop(0,"#010308"); g.addColorStop(.55,"#040b14"); g.addColorStop(1,"#071a26");
  ctx.fillStyle=g; ctx.fillRect(0,0,W,H);
  const off=(cam.yaw*120)%W;
  for(const s of stars){ const tw=.55+.45*Math.sin(t*s.s+s.p); ctx.fillStyle=`rgba(190,220,255,${.25+.55*tw*s.r/1.4})`; let x=((s.x*W-off)%W+W)%W; ctx.fillRect(x,s.y*H,s.r,s.r); }
  const hy=H/2-B.F*Math.tan(cam.pitch);          // where the horizon would be
  if(hy>-60&&hy<H+60){
    const a=ctx.createLinearGradient(0,hy-90,0,hy+30); a.addColorStop(0,"rgba(94,234,212,0)"); a.addColorStop(.7,"rgba(94,234,212,.22)"); a.addColorStop(.85,"rgba(120,190,255,.28)"); a.addColorStop(1,"rgba(2,5,10,.95)");
    ctx.fillStyle=a; ctx.fillRect(0,hy-90,W,120); ctx.fillStyle="rgba(2,5,10,.95)"; ctx.fillRect(0,hy+30,W,H);
  }
}
function drawGround(t){
  const {minx,maxx,minz,maxz}=geo;
  const c=[[minx,0,minz],[maxx,0,minz],[maxx,0,maxz],[minx,0,maxz]].map(p=>P(...p));
  if(c.every(Boolean)){
    ctx.fillStyle="rgba(12,48,66,.30)"; ctx.beginPath(); c.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1])); ctx.closePath(); ctx.fill();
    ctx.strokeStyle="rgba(94,234,212,.45)"; ctx.lineWidth=1.2; ctx.stroke();
    ctx.strokeStyle="rgba(94,234,212,.9)"; ctx.lineWidth=2;                      // corner brackets
    c.forEach((p,i)=>{ const q=c[(i+1)%4],r=c[(i+3)%4]; for(const n of [q,r]){ const d=Math.hypot(n[0]-p[0],n[1]-p[1])||1; ctx.beginPath(); ctx.moveTo(p[0]+(n[0]-p[0])/d*14,p[1]+(n[1]-p[1])/d*14); ctx.lineTo(p[0],p[1]); ctx.stroke(); } });
  }
  ctx.font="10px ui-monospace,Consolas,monospace"; ctx.textAlign="center";
  for(let lon=Math.ceil(toLon(minx)*4)/4; lon<=toLon(maxx); lon+=.25){
    const x=(lon-geo.lon0)*geo.k, major=Math.abs(lon*2-Math.round(lon*2))<1e-6;
    line([x,0,minz],[x,0,maxz],`rgba(110,190,220,${major?.26:.11})`,major?1.1:.7);
    if(major){ const p=P(x,0,minz); if(p){ ctx.fillStyle="rgba(150,200,225,.7)"; ctx.fillText(Math.abs(lon).toFixed(1)+"°W",p[0],p[1]+13); } }
  }
  for(let lat=Math.ceil(toLat(minz)*4)/4; lat<=toLat(maxz); lat+=.25){
    const z=(lat-geo.lat0)*110.57, major=Math.abs(lat*2-Math.round(lat*2))<1e-6;
    line([minx,0,z],[maxx,0,z],`rgba(110,190,220,${major?.26:.11})`,major?1.1:.7);
    if(major){ const p=P(minx,0,z); if(p){ ctx.textAlign="right"; ctx.fillStyle="rgba(150,200,225,.7)"; ctx.fillText(lat.toFixed(1)+"°N",p[0]-6,p[1]+3); ctx.textAlign="center"; } }
  }
  const cx=selected?selected.x:geo.cx, cz=selected?selected.z:geo.cz;
  for(const r of [25,50,75,100]){ if(r>geo.span*.9) break; strokePts(ring(cx,0,cz,r,96),"rgba(94,234,212,.2)",1);
    const p=P(cx+r*.7071,0,cz-r*.7071); if(p){ ctx.fillStyle="rgba(94,234,212,.55)"; ctx.fillText(r+" km",p[0],p[1]-3); } }
  // radar sweep
  const ang=(t*.55)%TAU, R=Math.min(geo.span*.8,110), N=40, da=.045, c0=P(cx,0,cz);
  if(c0) for(let i=0;i<N;i++){
    const a1=ang-i*da,a2=ang-(i+1)*da,p1=P(cx+Math.cos(a1)*R,0,cz+Math.sin(a1)*R),p2=P(cx+Math.cos(a2)*R,0,cz+Math.sin(a2)*R); if(!p1||!p2) continue;
    ctx.fillStyle=`rgba(94,234,212,${.16*Math.pow(1-i/N,2)})`; ctx.beginPath(); ctx.moveTo(c0[0],c0[1]); ctx.lineTo(p1[0],p1[1]); ctx.lineTo(p2[0],p2[1]); ctx.fill();
  }
  line([cx,0,cz],[cx+Math.cos(ang)*R,0,cz+Math.sin(ang)*R],"rgba(140,255,230,.7)",1.4);
  return {ang,cx,cz,R};
}
function ping(g,sw){
  const dx=g.x-sw.cx,dz=g.z-sw.cz; if(Math.hypot(dx,dz)>sw.R) return 0;
  const d=(((sw.ang-Math.atan2(dz,dx))%TAU)+TAU)%TAU; return Math.exp(-d*2.4);
}
function drawGauge(g,t,sw){
  const col=SC[g.status]||SC.nodata, has=g.stage!=null, sev=SEV[g.status]||0, pg=ping(g,sw);
  const base=P(g.x,0,g.z); if(!base) return;
  const alarm=alerts[g.site_no];
  strokePts(ring(g.x,0,g.z,1.7,28),rgba(col,has?.8:.35),has?1.6:1);
  if(!has){ ctx.fillStyle=rgba(col,.5); ctx.beginPath(); ctx.arc(base[0],base[1],2,0,TAU); ctx.fill(); hits.push({g,x:base[0],y:base[1],r:12}); return; }
  if(sev>=3||alarm){                                                            // expanding ground pulse
    const ph=((t*.6+g.x*.13)%1); strokePts(ring(g.x,0,g.z,2+ph*9,36),rgba(alarm?"#ff4d4d":col,.55*(1-ph)),1.5);
  }
  const h=hOf(g,g.stage), top=P(g.x,h,g.z); if(!top) return;
  const w=Math.max(1.6,.42*B.F/base[2]);
  ctx.save(); ctx.globalCompositeOperation="lighter";
  const gr=ctx.createLinearGradient(base[0],base[1],top[0],top[1]); gr.addColorStop(0,rgba(col,.06)); gr.addColorStop(1,rgba(col,.85));
  ctx.strokeStyle=rgba(col,.16+.2*pg); ctx.lineWidth=w*4; ctx.beginPath(); ctx.moveTo(base[0],base[1]); ctx.lineTo(top[0],top[1]); ctx.stroke();
  ctx.strokeStyle=gr; ctx.lineWidth=w; ctx.stroke();
  ctx.restore();
  for(const c of CATS){ const lv=g.cats&&g.cats[c]; if(lv==null) continue;      // official stages as ticks on the pillar
    const hh=hOf(g,lv), a=P(g.x-B.r[0]*1.1,hh,g.z-B.r[2]*1.1), b=P(g.x+B.r[0]*1.1,hh,g.z+B.r[2]*1.1);
    if(a&&b){ ctx.strokeStyle=rgba(SC[c],g.stage>=lv?.95:.5); ctx.lineWidth=g.stage>=lv?2:1; ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke(); } }
  const or=Math.max(3,.9*B.F/top[2]*(1+.25*pg)+(sev>=3?Math.sin(t*4)*.6:0));
  ctx.save(); ctx.globalCompositeOperation="lighter";
  const og=ctx.createRadialGradient(top[0],top[1],0,top[0],top[1],or*3.2); og.addColorStop(0,rgba(col,.95)); og.addColorStop(.25,rgba(col,.45)); og.addColorStop(1,rgba(col,0));
  ctx.fillStyle=og; ctx.beginPath(); ctx.arc(top[0],top[1],or*3.2,0,TAU); ctx.fill(); ctx.restore();
  ctx.fillStyle="#fff"; ctx.beginPath(); ctx.arc(top[0],top[1],Math.max(1.5,or*.45),0,TAU); ctx.fill();
  if(alarm){ const sky=P(g.x,h+geo.hmax*.9,g.z); if(sky){ ctx.setLineDash([4,4]); ctx.strokeStyle="rgba(255,90,80,.8)"; ctx.lineWidth=1.3; ctx.beginPath(); ctx.moveTo(top[0],top[1]); ctx.lineTo(sky[0],sky[1]); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle="#ff6b5e"; ctx.font="bold 10px ui-monospace,Consolas,monospace"; ctx.textAlign="center"; ctx.fillText("ALERT "+String(alarm).toUpperCase(),sky[0],sky[1]-6); } }
  if(g===selected){ ctx.strokeStyle="#fff"; ctx.lineWidth=1.5; const q=or*3.2; ctx.beginPath(); ctx.arc(top[0],top[1],q,0,TAU); ctx.stroke(); }
  if(g===selected||g===hover||sev>=3||cam.dist<geo.span*1.1){ ctx.font="11px ui-monospace,Consolas,monospace"; ctx.textAlign="left"; ctx.fillStyle="rgba(5,10,16,.85)"; ctx.fillText(g.lid,top[0]+or*2.3+1,top[1]+4); ctx.fillStyle="#e6f1fb"; ctx.fillText(g.lid,top[0]+or*2.3,top[1]+3); }
  hits.push({g,x:top[0],y:top[1],r:Math.max(12,or*2.2),base});
}
function drawHud(){
  const cx=64,cy=H-70; ctx.save(); ctx.translate(cx,cy);                       // compass
  ctx.strokeStyle="rgba(94,234,212,.5)"; ctx.lineWidth=1; ctx.beginPath(); ctx.arc(0,0,28,0,TAU); ctx.stroke();
  const a=-cam.yaw; ctx.rotate(a); ctx.fillStyle="#ff6b5e"; ctx.beginPath(); ctx.moveTo(0,-26); ctx.lineTo(5,0); ctx.lineTo(-5,0); ctx.fill();
  ctx.fillStyle="rgba(200,230,255,.6)"; ctx.beginPath(); ctx.moveTo(0,26); ctx.lineTo(5,0); ctx.lineTo(-5,0); ctx.fill();
  ctx.rotate(-a); ctx.fillStyle="#e6f1fb"; ctx.font="10px ui-monospace,Consolas,monospace"; ctx.textAlign="center"; ctx.fillText("N",Math.sin(-a)*38,-Math.cos(-a)*38+3); ctx.restore();
  const kmPx=cam.dist/B.F, nice=[5,10,25,50,100].find(n=>n/kmPx>70)||100, len=nice/kmPx;   // scale bar at target distance
  ctx.strokeStyle="rgba(94,234,212,.8)"; ctx.lineWidth=2; ctx.beginPath(); const bx=W-300-len,by=H-24; ctx.moveTo(bx,by-5); ctx.lineTo(bx,by); ctx.lineTo(bx+len,by); ctx.lineTo(bx+len,by-5); ctx.stroke();
  ctx.fillStyle="rgba(150,200,225,.8)"; ctx.font="10px ui-monospace,Consolas,monospace"; ctx.textAlign="center"; ctx.fillText(nice+" km (at centre)",bx+len/2,by-9);
}

/* ---------------------------------------------------------------- loop */
function frame(now){
  raf=requestAnimationFrame(frame); const dt=Math.min(.1,(now-lastT)/1000); lastT=now;
  if(!W||!H) return; if(!geo){ ctx.fillStyle="#02050a"; ctx.fillRect(0,0,W,H); ctx.fillStyle="#7f93a8"; ctx.font="13px ui-monospace,Consolas,monospace"; ctx.textAlign="center"; ctx.fillText(note||"waiting for gauge positions…",W/2,H/2); return; }
  if(cam.auto) cam.yaw+=dt*.045;
  const t=now/1000; basis(); hits=[]; drawSky(t); const sw=drawGround(t);
  const order=G.filter(g=>g.lat!=null).map(g=>({g,z:(P(g.x,0,g.z)||[0,0,1e9])[2]})).sort((a,b)=>b.z-a.z);
  for(const o of order) drawGauge(o.g,t,sw);
  drawHud();
  hudClock.textContent=new Date().toISOString().slice(11,19)+" UTC";
}
function resize(){ const r=root.getBoundingClientRect(); dpr=Math.min(2,window.devicePixelRatio||1); W=Math.round(r.width); H=Math.round(r.height); cv.width=W*dpr; cv.height=H*dpr; ctx.setTransform(dpr,0,0,dpr,0,0); }

/* ---------------------------------------------------------------- data + panels */
const ageTxt=s=>s==null?"":(s<90?Math.round(s)+" s ago":(s<5400?Math.round(s/60)+" min ago":(s/3600).toFixed(1)+" h ago"));
async function pollGauges(){
  try{
    const r=await fetch("/api/gauges?mode="+mode); if(!r.ok){ note="console server answered "+r.status+": restart python -m dashboard.flow_monitor"; return; }
    const d=await r.json();
    if(!d.locations){ note="no gauge locations yet: run python -m tools.gauge_locations"; return; }
    const gs=d.gauges.filter(g=>g.lat!=null); if(!gs.length){ note="no gauges"; return; }
    if(!geo) buildGeo(gs); else gs.forEach(g=>{ g.x=(g.lon-geo.lon0)*geo.k; g.z=(g.lat-geo.lat0)*110.57; });
    G=gs; if(selected) selected=G.find(g=>g.site_no===selected.site_no)||null;
    const n=G.filter(g=>g.stage!=null).length, hot=G.filter(g=>(SEV[g.status]||0)>=3).length;
    note=""; hudMode.innerHTML=mode==="replay"?"<b style='color:#ffb86b'>SIMULATION · replay</b>":"<b>LIVE</b>";
    hudStat.innerHTML=`<b>${n}</b>/${G.length} gauges reporting · <b style="color:${hot?"#ff8a3d":"inherit"}">${hot}</b> at or above minor flood`+(n===0&&mode==="replay"?"<br>no replay readings seen yet: start a replay":"");
    drawList(); drawCard();
  }catch(e){ note="cannot reach the console server"; }
}
async function pollAlerts(){
  try{ const d=await (await fetch("/api/advisories")).json(); if(!d.ok) return; const a={};
    const want=mode==="replay"?"SIMULATION":"LIVE";
    for(const x of d.advisories){ if(x.mode===want&&["draft","approved","sent"].includes(x.status)&&x.alert_status!=="retracted"&&x.alert_status!=="rejected") a[x.site_no]=x.category; }
    alerts=a; }catch(e){}
}
function drawList(){
  list.innerHTML="";
  G.slice().sort((a,b)=>(SEV[b.status]-SEV[a.status])||a.name.localeCompare(b.name)).forEach(g=>{
    const d=document.createElement("div"); d.className="grow"+(g===selected?" sel":"");
    d.innerHTML=`<span class="gdot" style="color:${SC[g.status]};background:${SC[g.status]}"></span><div><div class="nm"></div><div class="sub"></div></div>`;
    d.querySelector(".nm").textContent=g.name;
    d.querySelector(".sub").textContent=(g.stage==null?"no reading":g.stage.toFixed(2)+" ft")+(g.age_s!=null?" · "+ageTxt(g.age_s):"")+" · "+g.status;
    d.onclick=()=>select(g); list.appendChild(d);
  });
}
function drawCard(){
  const g=selected;
  if(!g){ const c={}; G.forEach(x=>c[x.status]=(c[x.status]||0)+1);
    card.innerHTML=`<div class="sub">NETWORK</div><div class="nm" style="margin:2px 0 8px">${G.length} gauges · ${mode==="replay"?"SIMULATION replay":"live"}</div>`+
      Object.keys(SEV).filter(k=>c[k]).map(k=>`<span class="pill" style="color:${SC[k]};border-color:${SC[k]}55">${c[k]} ${k}</span>`).join("")+
      `<div class="note" style="margin-top:8px">drag to orbit · wheel to zoom · click a pillar<br>pillar height = stage vs that gauge's own official major level; ticks mark the official stages</div>`; return; }
  const cs=g.cats||{}, top=Math.max(cs.major||0,cs.moderate||0,g.stage||0)*1.08||10, pct=v=>Math.min(100,Math.max(0,v/top*100));
  const ticks=CATS.filter(c=>cs[c]!=null).map(c=>`<u style="left:${pct(cs[c])}%;color:${SC[c]}">${c.slice(0,3)} ${cs[c]}</u>`).join("");
  card.innerHTML=`<div class="sub">${g.lid} · ${g.site_no}${g.county?" · "+g.county+" Co.":""}</div><div class="nm">${g.name}</div>`+
    `<div class="val" style="color:${SC[g.status]}">${g.stage==null?"—":g.stage.toFixed(2)}<small> ft</small></div>`+
    `<div class="sub" style="color:${SC[g.status]}">${g.status.toUpperCase()}${g.provisional?" · provisional":""}${g.age_s!=null?" · "+ageTxt(g.age_s):""}</div>`+
    `<div class="lvl">${g.stage!=null?`<i style="left:calc(${pct(g.stage)}% - 1px)"></i>`:""}${ticks}</div><div style="height:12px"></div>`+
    (alerts[g.site_no]?`<div class="pill" style="color:#ff6b5e;border-color:#ff6b5e88">alert: ${alerts[g.site_no]}</div>`:"");
}
function select(g){ selected=g; drawList(); drawCard(); }

/* ---------------------------------------------------------------- input */
let drag=null;
cv.addEventListener("pointerdown",e=>{ cv.setPointerCapture(e.pointerId); drag={x:e.clientX,y:e.clientY,moved:0}; cv.classList.add("drag"); });
cv.addEventListener("pointermove",e=>{
  const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
  if(drag){ const dx=e.clientX-drag.x,dy=e.clientY-drag.y; drag.moved+=Math.abs(dx)+Math.abs(dy); drag.x=e.clientX; drag.y=e.clientY;
    cam.yaw-=dx*.006; cam.pitch=Math.max(.12,Math.min(1.5,cam.pitch+dy*.005)); cam.auto=false; syncAuto(); tip.hidden=true; return; }
  let best=null,bd=1e9; for(const h of hits){ const d=Math.hypot(h.x-mx,h.y-my); if(d<h.r&&d<bd){ best=h; bd=d; } }
  hover=best?best.g:null; cv.style.cursor=best?"pointer":"grab";
  if(best){ tip.hidden=false; tip.style.left=mx+"px"; tip.style.top=my+"px"; tip.textContent=`${best.g.name} · ${best.g.stage==null?"no reading":best.g.stage.toFixed(2)+" ft"} · ${best.g.status}`; } else tip.hidden=true;
});
cv.addEventListener("pointerup",e=>{ cv.classList.remove("drag"); if(drag&&drag.moved<5){ const r=cv.getBoundingClientRect(); const mx=e.clientX-r.left,my=e.clientY-r.top; let best=null,bd=1e9;
  for(const h of hits){ const d=Math.hypot(h.x-mx,h.y-my); if(d<h.r&&d<bd){ best=h; bd=d; } } select(best?best.g:null); } drag=null; });
cv.addEventListener("pointerleave",()=>{ hover=null; tip.hidden=true; });
cv.addEventListener("wheel",e=>{ e.preventDefault(); if(!geo) return; cam.dist=Math.max(geo.span*.3,Math.min(geo.span*5,cam.dist*Math.exp(e.deltaY*.0012))); },{passive:false});
cv.addEventListener("dblclick",()=>{ if(geo){ cam.dist=geo.span*1.55; cam.pitch=.62; } });
function syncAuto(){ const b=document.getElementById("o-auto"); if(b) b.setAttribute("aria-pressed",cam.auto); }
const bind=(id,fn)=>{ const b=document.getElementById(id); if(b) b.onclick=fn; };
bind("o-auto",()=>{ cam.auto=!cam.auto; syncAuto(); });
bind("o-top",()=>{ cam.pitch=1.5; });
bind("o-tilt",()=>{ cam.pitch=.62; });
bind("o-low",()=>{ cam.pitch=.2; });
bind("o-live",()=>{ mode="live"; setSeg(true); geo=null; G=[]; pollGauges(); });
bind("o-replay",()=>{ mode="replay"; setSeg(false); geo=null; G=[]; pollGauges(); });
function setSeg(live){ document.getElementById("o-live").setAttribute("aria-pressed",live); document.getElementById("o-replay").setAttribute("aria-pressed",!live); }

window.Orbit={
  show(){ if(running) return; running=true; resize(); lastT=performance.now(); raf=requestAnimationFrame(frame); pollGauges(); pollAlerts(); pollA=setInterval(pollGauges,4000); pollB=setInterval(pollAlerts,6000); },
  hide(){ running=false; cancelAnimationFrame(raf); clearInterval(pollA); clearInterval(pollB); }
};
if(!document.getElementById("tab-orbit").hidden) window.Orbit.show();      // the page script may have opened this tab before we loaded
if(window.ResizeObserver) new ResizeObserver(()=>{ if(running) resize(); }).observe(root); else addEventListener("resize",()=>{ if(running) resize(); });
})();
