/* Globe: an orthographic sphere drawn per pixel from an equirectangular texture (Esri World Imagery, muted), with an
   approximate day/night terminator (sub-solar point from the UTC date, no equation of time), atmosphere glow, a 30 degree
   graticule and one marker per monitored region. Add a region in config/regions.json and it appears here.
   Used by map3d.js; no library. */
(function(){
"use strict";
const TAU=Math.PI*2, D2R=Math.PI/180;
const TXW=2048, TXH=1024;
const st={lon:-98.3*D2R,lat:28*D2R,zoom:1,fly:null,auto:true};
let raw=null,tex=null,texDark=null,texVer=0,loading=false,buf=null,bctx=null,img=null,u32=null,hits=[];

/* ---------------------------------------------------------------- texture */
const lat2y=(lat,z)=>{ const s=Math.sin(lat*D2R); return (.5-Math.log((1+s)/(1-s))/(4*Math.PI))*Math.pow(2,z); };
function loadImg(u){ return new Promise(res=>{ const im=new Image(); im.crossOrigin="anonymous"; im.onload=()=>res(im); im.onerror=()=>res(null); im.src=u; }); }
async function loadRaw(){
  if(raw||loading) return; loading=true;
  const z=3,n=8,m=document.createElement("canvas"); m.width=m.height=n*256; const g=m.getContext("2d"); let ok=0;
  await Promise.all(Array.from({length:n*n},(_,i)=>{ const x=i%n,y=Math.floor(i/n);
    return loadImg(`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`).then(im=>{ if(im){ g.drawImage(im,x*256,y*256); ok++; } }); }));
  loading=false; if(!ok){ raw=false; return; }
  const t=document.createElement("canvas"); t.width=TXW; t.height=TXH; const c=t.getContext("2d"); c.fillStyle="#05090f"; c.fillRect(0,0,TXW,TXH);
  for(let j=0;j<TXH;j+=2){ const la=90-j/TXH*180, lb=90-(j+2)/TXH*180; if(la>85.0||lb<-85.0) continue;
    const sy=lat2y(la,z)*256, sh=(lat2y(lb,z)-lat2y(la,z))*256; c.drawImage(m,0,sy,TXW,sh,0,j,TXW,3); }
  raw=c.getImageData(0,0,TXW,TXH); tex=null; texVer++;
}
function tint(dark){                        // muted look: desaturate, darken for the dark theme
  const src=new Uint32Array(raw.data.buffer), out=new Uint32Array(src.length), sat=dark?.55:.7, mul=dark?.78:1;
  for(let i=0;i<src.length;i++){ const c=src[i], r=c&255, g=(c>>8)&255, b=(c>>16)&255, l=.3*r+.59*g+.11*b;
    const R=Math.min(255,(l+(r-l)*sat)*mul), G=Math.min(255,(l+(g-l)*sat)*mul), Bb=Math.min(255,(l+(b-l)*sat)*mul*(dark?1.08:1));
    out[i]=(255<<24)|(Bb<<16)|(G<<8)|R; }
  return out;
}

/* ---------------------------------------------------------------- sun + projection */
function subsolar(d){
  const day=(d-Date.UTC(d.getUTCFullYear(),0,0))/864e5, decl=-23.44*Math.cos(TAU/365*(day+10))*D2R, h=d.getUTCHours()+d.getUTCMinutes()/60+d.getUTCSeconds()/3600;
  return {lon:-(h-12)*15*D2R,lat:decl};
}
function toView(lon,lat){                    // world (lon,lat) -> view coords (x right, y up, z toward the viewer)
  const dl=lon-st.lon, cl=Math.cos(lat), s0=Math.sin(st.lat), c0=Math.cos(st.lat);
  return [cl*Math.sin(dl), c0*Math.sin(lat)-s0*cl*Math.cos(dl), s0*Math.sin(lat)+c0*cl*Math.cos(dl)];
}
function fromScreen(px,py,cx,cy,R){          // screen -> [lon,lat] or null outside the disc
  const nx=(px-cx)/R, ny=(cy-py)/R, r2=nx*nx+ny*ny; if(r2>1) return null;
  const z=Math.sqrt(1-r2), s0=Math.sin(st.lat), c0=Math.cos(st.lat);
  return [st.lon+Math.atan2(nx,z*c0-ny*s0), Math.asin(z*s0+ny*c0)];
}
const radius=(W,H)=>Math.min(W,H)*.36*st.zoom;

/* ---------------------------------------------------------------- draw */
function disc(R,scale,dark){
  const S=Math.max(96,Math.min(820,Math.round(2*R*scale)));
  if(!buf||buf.width!==S){ buf=document.createElement("canvas"); buf.width=buf.height=S; bctx=buf.getContext("2d"); img=bctx.createImageData(S,S); u32=new Uint32Array(img.data.buffer); }
  if(!tex||texDark!==dark){ tex=tint(dark); texDark=dark; }
  const sp=subsolar(new Date()), sv=toView(sp.lon,sp.lat), s0=Math.sin(st.lat), c0=Math.cos(st.lat), half=S/2, night=dark?.3:.55, lon0=st.lon;
  let k=0;
  for(let py=0;py<S;py++){ const ny=(half-py-.5)/half;
    for(let px=0;px<S;px++,k++){ const nx=(px+.5-half)/half, r2=nx*nx+ny*ny; if(r2>1){ u32[k]=0; continue; }
      const z=Math.sqrt(1-r2), lat=Math.asin(z*s0+ny*c0); let lon=lon0+Math.atan2(nx,z*c0-ny*s0);
      let u=(lon+Math.PI)/TAU; u-=Math.floor(u); const v=(Math.PI/2-lat)/Math.PI;
      const c=tex[((v*TXH)|0)*TXW+((u*TXW)|0)], dot=nx*sv[0]+ny*sv[1]+z*sv[2], t=Math.max(0,Math.min(1,(dot+.08)/.3)), day=t*t*(3-2*t);
      const lum=(night+(1-night)*day)*(.55+.45*Math.pow(z,.45)), r=c&255, g=(c>>8)&255, b=(c>>16)&255;
      u32[k]=(255<<24)|(Math.min(255,b*lum*(1+(1-day)*.18))<<16)|(Math.min(255,g*lum)<<8)|Math.min(255,r*lum*(1-(1-day)*.1)); } }
  bctx.putImageData(img,0,0); return buf;
}
function draw(ctx,W,H,t,T,regions,sel,L,cx0,quality){
  if(raw===null) loadRaw();
  const R=radius(W,H), cx=cx0, cy=H/2-6;
  // atmosphere
  const ag=ctx.createRadialGradient(cx,cy,R*.96,cx,cy,R*1.22); ag.addColorStop(0,T.dark?"rgba(120,160,200,.38)":"rgba(70,110,150,.30)"); ag.addColorStop(.35,T.dark?"rgba(90,130,175,.12)":"rgba(70,110,150,.10)"); ag.addColorStop(1,"rgba(0,0,0,0)");
  ctx.fillStyle=ag; ctx.beginPath(); ctx.arc(cx,cy,R*1.22,0,TAU); ctx.fill();
  if(raw){ const b=disc(R,quality||.55,T.dark); ctx.save(); ctx.beginPath(); ctx.arc(cx,cy,R,0,TAU); ctx.clip(); ctx.imageSmoothingEnabled=true; ctx.drawImage(b,cx-R,cy-R,2*R,2*R); ctx.restore(); }
  else { ctx.fillStyle=T.dark?"#0b1520":"#cfd8e2"; ctx.beginPath(); ctx.arc(cx,cy,R,0,TAU); ctx.fill(); ctx.fillStyle=T.muted; ctx.font="12px Inter,system-ui,sans-serif"; ctx.textAlign="center"; ctx.fillText(raw===false?"globe imagery unavailable (offline?)":"loading globe imagery…",cx,cy); }
  ctx.strokeStyle=T.dark?"rgba(150,185,220,.55)":"rgba(40,70,100,.5)"; ctx.lineWidth=1; ctx.beginPath(); ctx.arc(cx,cy,R,0,TAU); ctx.stroke();
  // graticule every 30 degrees
  if(L.grid){ ctx.lineWidth=.7; ctx.strokeStyle=T.dark?"rgba(190,210,235,.16)":"rgba(30,50,80,.18)";
    for(let lon=-180;lon<180;lon+=30){ ctx.beginPath(); let pen=false; for(let lat=-90;lat<=90;lat+=3){ const v=toView(lon*D2R,lat*D2R); if(v[2]<=0){ pen=false; continue; } const x=cx+v[0]*R,y=cy-v[1]*R; pen?ctx.lineTo(x,y):ctx.moveTo(x,y); pen=true; } ctx.stroke(); }
    for(let lat=-60;lat<=60;lat+=30){ ctx.beginPath(); let pen=false; for(let lon=-180;lon<=180;lon+=3){ const v=toView(lon*D2R,lat*D2R); if(v[2]<=0){ pen=false; continue; } const x=cx+v[0]*R,y=cy-v[1]*R; pen?ctx.lineTo(x,y):ctx.moveTo(x,y); pen=true; } ctx.stroke(); } }
  // regions
  hits=[];
  if(L.regions!==false) for(const r of regions){
    const v=toView(r.lon*D2R,r.lat*D2R); if(v[2]<=.02) continue; const x=cx+v[0]*R,y=cy-v[1]*R, col=T.sc[r.worst]||T.sc.nodata, hot=r.n_flooding>0, isSel=sel&&sel.id===r.id;
    const ph=(t*.5)%1; ctx.strokeStyle=col; ctx.globalAlpha=.55*(1-ph); ctx.lineWidth=1.4; ctx.beginPath(); ctx.arc(x,y,6+ph*20,0,TAU); ctx.stroke(); ctx.globalAlpha=1;
    ctx.fillStyle=col; ctx.beginPath(); ctx.arc(x,y,hot?5:4,0,TAU); ctx.fill(); ctx.strokeStyle=T.dark?"#0b1118":"#fff"; ctx.lineWidth=1.5; ctx.stroke();
    ctx.strokeStyle=T.ink; ctx.lineWidth=1; const q=isSel?13:10; for(const [dx,dy] of [[-1,-1],[1,-1],[1,1],[-1,1]]){ ctx.beginPath(); ctx.moveTo(x+dx*q,y+dy*(q-5)); ctx.lineTo(x+dx*q,y+dy*q); ctx.lineTo(x+dx*(q-5),y+dy*q); ctx.stroke(); }   // target brackets
    if(L.labels!==false){ ctx.font="600 11px Inter,system-ui,sans-serif"; ctx.textAlign="left"; ctx.lineWidth=3; ctx.strokeStyle=T.dark?"rgba(6,10,15,.85)":"rgba(255,255,255,.9)"; ctx.strokeText(r.name.toUpperCase(),x+18,y+4); ctx.fillStyle=T.ink; ctx.fillText(r.name.toUpperCase(),x+18,y+4);
      ctx.font="10px Inter,system-ui,sans-serif"; ctx.fillStyle=T.muted; ctx.fillText(`${r.n_reporting}/${r.n_gauges} gauges${r.n_flooding?" · "+r.n_flooding+" in flood":""}`,x+18,y+17); }
    hits.push({region:r,x,y,r:20});
  }
  return {cx,cy,R};
}

/* ---------------------------------------------------------------- state, input, flight */
function step(dt,interacting){
  if(st.fly){ const f=st.fly; f.t=Math.min(1,f.t+dt/f.dur); const e=f.t<.5?2*f.t*f.t:1-Math.pow(-2*f.t+2,2)/2;
    st.lon=f.lon0+(f.lon1-f.lon0)*e; st.lat=f.lat0+(f.lat1-f.lat0)*e; st.zoom=Math.exp(Math.log(f.z0)+(Math.log(f.z1)-Math.log(f.z0))*e);
    if(f.t>=1){ const d=f.done; st.fly=null; if(d) d(); } }
  else if(st.auto&&!interacting) st.lon+=dt*.05;
}
function wrap(a){ while(a>Math.PI) a-=TAU; while(a<-Math.PI) a+=TAU; return a; }
function fly(lonDeg,latDeg,zoom,ms,done){
  const l1=lonDeg*D2R, dl=wrap(l1-st.lon); st.fly={lon0:st.lon,lat0:st.lat,z0:st.zoom,lon1:st.lon+dl,lat1:latDeg*D2R,z1:zoom,t:0,dur:ms/1000,done}; st.auto=false;
}
window.Globe={
  st, draw, step, fly, radius, fromScreen, toView, hits:()=>hits, texVer:()=>texVer,
  drag(dx,dy,W,H){ const R=radius(W,H); st.lon-=dx/R/Math.max(.35,Math.cos(st.lat)); st.lat=Math.max(-1.35,Math.min(1.35,st.lat+dy/R)); st.auto=false; st.fly=null; },
  zoom(dy){ st.zoom=Math.max(.6,Math.min(9,st.zoom*Math.exp(-dy*.0012))); },
  setView(lonDeg,latDeg,zoom){ st.lon=lonDeg*D2R; st.lat=latDeg*D2R; st.zoom=zoom; st.fly=null; },
  sunDeg(){ const s=subsolar(new Date()); return {lon:s.lon/D2R,lat:s.lat/D2R}; }
};
})();
