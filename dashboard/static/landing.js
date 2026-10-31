/* Landing page: the map's own canvas shows the Earth low in the frame; entering moves the same Earth to the centre of the globe view,
   so there is no page change. Figures are read from the console APIs; a figure that cannot be read stays as a dash. */
(function(){
"use strict";
const $=id=>document.getElementById(id), body=document.body, TABS=window.CONSOLE_TABS;
const reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;
let stats=0;
async function loadStats(){
  try{
    const d=await (await fetch("/api/regions?mode=live")).json(), rs=(d.regions||[]).filter(r=>!r.planned);
    $("ls-areas").textContent=rs.length; $("ls-gauges").textContent=rs.reduce((a,r)=>a+r.n_gauges,0); $("ls-reporting").textContent=rs.reduce((a,r)=>a+r.n_reporting,0);
  }catch(e){}
  try{ const d=await (await fetch("/api/advisories")).json(); if(d.ok) $("ls-pending").textContent=d.advisories.filter(a=>a.status==="draft").length; }catch(e){}
}
function goLanding(instant){
  for(const n of TABS){ $("tab-"+n).hidden=n!=="map"; $("tb-"+n).setAttribute("aria-selected",false); }
  body.classList.add("landing","on-map"); try{ history.replaceState(null,"",location.pathname); }catch(e){}
  Map3D.show(); Map3D.toLanding(instant); loadStats(); clearInterval(stats); stats=setInterval(loadStats,8000);
}
function leave(tab){
  clearInterval(stats); body.classList.remove("landing");
  Map3D.fromLanding(tab==="map"&&!reduce?1900:1);
  showTab(tab);
}
$("l-enter").addEventListener("click",()=>leave("map"));
$("brand").addEventListener("click",()=>{ if(!body.classList.contains("landing")) goLanding($("tab-map").hidden); });     // from another tab there is no map on screen to move
$("landing").addEventListener("wheel",e=>{ if(e.deltaY>10&&body.classList.contains("landing")) leave("map"); },{passive:true});
let touchY=null;
$("landing").addEventListener("touchstart",e=>{ touchY=e.touches[0].clientY; },{passive:true});
$("landing").addEventListener("touchmove",e=>{ if(touchY!=null&&touchY-e.touches[0].clientY>40&&body.classList.contains("landing")){ touchY=null; leave("map"); } },{passive:true});
addEventListener("keydown",e=>{ if(e.key==="Enter"&&body.classList.contains("landing")&&document.activeElement===body) leave("map"); });
const h=(location.hash||"").slice(1);
if(TABS.includes(h)) showTab(h); else goLanding(true);
addEventListener("hashchange",()=>{ const t=(location.hash||"").slice(1); if(TABS.includes(t)){ if(body.classList.contains("landing")) leave(t); else showTab(t); } });
})();
