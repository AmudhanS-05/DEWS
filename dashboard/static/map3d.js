/* Map view: one canvas. Globe (monitored areas as markers, double-click to fly in) -> region as 2D (top-down) or 3D (tilted,
   orbitable), optional Terrain. Ops-room HUD: status, data layers, scene brief, coordinates, visual presets.
   Ground  = Esri Light/Dark Gray canvas tiles (match the theme) draped on a plane; Terrain adds real elevation
             (AWS Terrain Tiles, decoded in the browser) as a displaced surface with hillshade and 100 m contours.
   Gauges  = pillars: height is the newest stage against THAT gauge's own official NWS major level, with ticks at the official
             action/minor/moderate/major stages. A radar sweep pings gauges, ground rings pulse at minor flood or above,
             a dashed red beam marks an open alert (only alerts of the mode on screen: live or SIMULATION replay).
   Data    = /api/gauges (stage, status, official levels), /api/advisories (alerts). No library: own perspective camera. */
(function(){
"use strict";
const $=id=>document.getElementById(id);
const root=$("map3d"), cv=$("m-canvas"), ctx=cv.getContext("2d");
const scene=document.createElement("canvas"), sctx=scene.getContext("2d");
const card=$("m-card"), list=$("m-list"), tip=$("m-tip");
const SEV={major:5,moderate:4,minor:3,action:2,normal:1,stale:0,nodata:0}, CATS=["action","minor","moderate","major"], TAU=Math.PI*2;
const EXAG=18;                      // vertical exaggeration of real terrain (km of height per km of true elevation x 1000)
let TH=null;
function readTheme(){
  const cs=getComputedStyle(document.documentElement), v=n=>cs.getPropertyValue(n).trim();
  TH={dark:document.documentElement.dataset.theme!=="light",ink:v("--ink"),muted:v("--muted"),line:v("--line2"),accent:v("--accent"),sky0:v("--sky0"),sky1:v("--sky1"),
      sc:Object.fromEntries(["normal","action","minor","moderate","major","stale","nodata"].map(k=>[k,v("--s-"+k)]))};
}
readTheme();
const rgba=(hex,a)=>{ const n=parseInt(hex.slice(1),16); return `rgba(${n>>16},${(n>>8)&255},${n&255},${a})`; };

let W=0,H=0,dpr=1,running=false,raf=0,lastT=performance.now();
const cam={yaw:0,pitch:.62,dist:150,tx:0,tz:0,auto:false};
let G=[],mode="live",selected=null,hover=null,alerts={},hits=[],geo=null,note="",pollA=0,pollB=0,terrain=false,interacting=false;
let cacheKey="",texVer=0;
let view="globe",regions=[],selRegion=null,regionId=null,initGlobe=false,fadeCv=null,fadeA=0,flying=false,lastHud=0,gInfo=null;
const L={gauges:true,alerts:true,sweep:true,rings:true,grid:true,labels:true,regions:true};   // data layers (panel on the left)

/* ---------------------------------------------------------------- geometry */
function buildGeo(gs){
  const lat0=gs.reduce((a,g)=>a+g.lat,0)/gs.length, lon0=gs.reduce((a,g)=>a+g.lon,0)/gs.length, k=111.32*Math.cos(lat0*Math.PI/180);
  gs.forEach(g=>{ g.x=(g.lon-lon0)*k; g.z=(lat0-g.lat)*110.57; });
  const xs=gs.map(g=>g.x), zs=gs.map(g=>g.z);
  const minx=Math.min(...xs),maxx=Math.max(...xs),minz=Math.min(...zs),maxz=Math.max(...zs);
  const span=Math.max(maxx-minx,maxz-minz,40), m=span*.18;
  geo={lat0,lon0,k,span,minx:minx-m,maxx:maxx+m,minz:minz-m,maxz:maxz+m,cx:(minx+maxx)/2,cz:(minz+maxz)/2,hmax:span*.2};
  geo.w=geo.maxx-geo.minx; geo.d=geo.maxz-geo.minz;
  geo.lonMin=toLon(geo.minx); geo.lonMax=toLon(geo.maxx); geo.latMax=toLat(geo.minz); geo.latMin=toLat(geo.maxz);
  cam.dist=span*1.55; cam.tx=cam.tz=0;
  baseTex=terrTex=null; dem=null; HG=null; texVer++; loadBase(); if(terrain) loadDem();
}
const toLon=x=>geo.lon0+x/geo.k, toLat=z=>geo.lat0-z/110.57;     // z grows southwards
function levelRef(g){ const c=g.cats||{}; return c.major||c.moderate||(c.minor?c.minor*1.4:null)||(c.action?c.action*1.8:20); }
const hOf=(g,s)=>Math.max(0,Math.min(1.35,s/levelRef(g)))*geo.hmax;

/* ---------------------------------------------------------------- tiles, elevation, textures */
const lon2x=(lon,z)=>(lon+180)/360*Math.pow(2,z);
const lat2y=(lat,z)=>{ const s=Math.sin(lat*Math.PI/180); return (.5-Math.log((1+s)/(1-s))/(4*Math.PI))*Math.pow(2,z); };
const imgCache=new Map();
function getImg(u){ if(!imgCache.has(u)) imgCache.set(u,new Promise(res=>{ const im=new Image(); im.crossOrigin="anonymous"; im.onload=()=>res(im); im.onerror=()=>res(null); im.src=u; })); return imgCache.get(u); }
const pickZoom=()=>Math.max(5,Math.min(11,Math.round(Math.log2(5*360/(geo.lonMax-geo.lonMin)))));
function tileRange(z){ return {z,x0:Math.floor(lon2x(geo.lonMin,z)),x1:Math.floor(lon2x(geo.lonMax,z)),y0:Math.floor(lat2y(geo.latMax,z)),y1:Math.floor(lat2y(geo.latMin,z))}; }
async function mosaic(R,urlOf){
  const nx=R.x1-R.x0+1, ny=R.y1-R.y0+1, jobs=[];
  for(let y=R.y0;y<=R.y1;y++) for(let x=R.x0;x<=R.x1;x++) jobs.push(getImg(urlOf(R.z,x,y)).then(im=>({im,x,y})));
  const done=await Promise.all(jobs), c=document.createElement("canvas"); c.width=nx*256; c.height=ny*256;
  const g=c.getContext("2d"); let ok=0; for(const t of done) if(t.im){ g.drawImage(t.im,(t.x-R.x0)*256,(t.y-R.y0)*256); ok++; }
  return ok?c:null;
}
let TEXW=1280,TEXH=800,baseTex=null,terrTex=null,baseVer=0,dem=null,HG=null,relief=null;
async function loadBase(){
  const ver=++baseVer, z=pickZoom(), R=tileRange(z), style=TH.dark?"World_Dark_Gray_Base":"World_Light_Gray_Base";
  TEXH=Math.max(300,Math.min(1600,Math.round(TEXW*geo.d/geo.w)));
  const m=await mosaic(R,(z,x,y)=>`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/${style}/MapServer/tile/${z}/${y}/${x}`);
  if(ver!==baseVer) return;
  const t=document.createElement("canvas"); t.width=TEXW; t.height=TEXH; const g=t.getContext("2d");
  g.fillStyle=TH.dark?"#161d26":"#e6ebf0"; g.fillRect(0,0,TEXW,TEXH);
  if(m){                                                   // mercator tiles -> linear-latitude texture, in thin strips
    const sx=(lon2x(geo.lonMin,z)-R.x0)*256, sw=(lon2x(geo.lonMax,z)-lon2x(geo.lonMin,z))*256, S=4;
    for(let j=0;j<TEXH;j+=S){
      const la=geo.latMax-j/TEXH*(geo.latMax-geo.latMin), lb=geo.latMax-(j+S)/TEXH*(geo.latMax-geo.latMin);
      const sy=(lat2y(la,z)-R.y0)*256, sh=(lat2y(lb,z)-lat2y(la,z))*256;
      g.drawImage(m,sx,sy,sw,sh,0,j,TEXW,S+1);
    }
  } else note="basemap tiles unavailable (offline?): showing the plain ground";
  baseTex=t; composeTerrain(); texVer++;
}
async function loadDem(){
  if(dem||dem===false) return; dem=false; note="loading elevation…";
  const z=Math.min(pickZoom(),10), R=tileRange(z), m=await mosaic(R,(z,x,y)=>`https://s3.amazonaws.com/elevation-tiles-prod/terrarium/${z}/${x}/${y}.png`);
  if(!m){ note="elevation tiles unavailable (offline?): terrain not shown"; dem=null; return; }
  const id=m.getContext("2d").getImageData(0,0,m.width,m.height).data, h=new Float32Array(m.width*m.height);
  for(let i=0;i<h.length;i++) h[i]=id[i*4]*256+id[i*4+1]+id[i*4+2]/256-32768;
  dem={R,w:m.width,h:m.height,data:h}; note="";
  buildRelief(); buildHeightGrid(); composeTerrain(); texVer++;
}
function elevAt(lon,lat){
  const D=dem, x=(lon2x(lon,D.R.z)-D.R.x0)*256, y=(lat2y(lat,D.R.z)-D.R.y0)*256;
  const xi=Math.max(0,Math.min(D.w-2,Math.floor(x))), yi=Math.max(0,Math.min(D.h-2,Math.floor(y))), fx=Math.max(0,Math.min(1,x-xi)), fy=Math.max(0,Math.min(1,y-yi)), d=D.data, w=D.w;
  return (d[yi*w+xi]*(1-fx)+d[yi*w+xi+1]*fx)*(1-fy)+(d[(yi+1)*w+xi]*(1-fx)+d[(yi+1)*w+xi+1]*fx)*fy;
}
function buildHeightGrid(){
  const N=48, g=new Float32Array((N+1)*(N+1)); let e0=1e9;
  for(let j=0;j<=N;j++) for(let i=0;i<=N;i++){ const e=elevAt(toLon(geo.minx+i/N*geo.w),toLat(geo.minz+j/N*geo.d)); g[j*(N+1)+i]=e; if(e<e0) e0=e; }
  for(let i=0;i<g.length;i++) g[i]=(g[i]-e0)/1000*EXAG;
  HG={N,g};
}
function groundY(x,z){
  if(!terrain||!HG) return 0;
  const N=HG.N,u=Math.max(0,Math.min(N-1e-6,(x-geo.minx)/geo.w*N)),v=Math.max(0,Math.min(N-1e-6,(z-geo.minz)/geo.d*N)),i=Math.floor(u),j=Math.floor(v),fx=u-i,fy=v-j,g=HG.g,s=N+1;
  return (g[j*s+i]*(1-fx)+g[j*s+i+1]*fx)*(1-fy)+(g[(j+1)*s+i]*(1-fx)+g[(j+1)*s+i+1]*fx)*fy;
}
function buildRelief(){                       // hillshade + contours as a transparent overlay, computed once per DEM
  const HW=900,HHt=Math.round(HW*geo.d/geo.w), E=new Float32Array(HW*HHt);
  for(let j=0;j<HHt;j++) for(let i=0;i<HW;i++) E[j*HW+i]=elevAt(geo.lonMin+(i+.5)/HW*(geo.lonMax-geo.lonMin),geo.latMax-(j+.5)/HHt*(geo.latMax-geo.latMin));
  const cx=geo.w*1000/HW, cy=geo.d*1000/HHt, ex=5, alt=40*Math.PI/180, az=315*Math.PI/180;
  const L=[Math.cos(alt)*Math.sin(az),Math.cos(alt)*Math.cos(az),Math.sin(alt)], flat=L[2];
  const c=document.createElement("canvas"); c.width=HW; c.height=HHt; const g=c.getContext("2d"), im=g.createImageData(HW,HHt), o=im.data;
  for(let j=1;j<HHt-1;j++) for(let i=1;i<HW-1;i++){
    const k=j*HW+i, gx=(E[k+1]-E[k-1])/(2*cx)*ex, gn=(E[k-HW]-E[k+HW])/(2*cy)*ex, inv=1/Math.sqrt(gx*gx+gn*gn+1);
    const s=(-gx*L[0]-gn*L[1]+L[2])*inv, d=s-flat, p=k*4;
    if(d<0){ o[p]=o[p+1]=o[p+2]=0; o[p+3]=Math.min(175,-d*560); } else { o[p]=o[p+1]=o[p+2]=255; o[p+3]=Math.min(130,d*420); }
    const a=Math.floor(E[k]/100), b=Math.floor(E[k+1]/100), c2=Math.floor(E[k+HW]/100);
    if(a!==b||a!==c2){ const idx=Math.floor(E[k]/500)!==Math.floor(E[k+1]/500)||Math.floor(E[k]/500)!==Math.floor(E[k+HW]/500);
      o[p]=40;o[p+1]=48;o[p+2]=58;o[p+3]=idx?120:55; }
  }
  g.putImageData(im,0,0); relief=c;
}
function composeTerrain(){
  if(!baseTex||!dem||!relief) return;
  const t=document.createElement("canvas"); t.width=baseTex.width; t.height=baseTex.height; const g=t.getContext("2d");
  g.drawImage(baseTex,0,0); g.globalAlpha=TH.dark?.9:.8; g.drawImage(relief,0,0,t.width,t.height); terrTex=t;
}

/* ---------------------------------------------------------------- camera */
let B=null;
function basis(){
  const cp=Math.cos(cam.pitch),sp=Math.sin(cam.pitch),cy=Math.cos(cam.yaw),sy=Math.sin(cam.yaw), tx=geo.cx+cam.tx, tz=geo.cz+cam.tz;
  B={pos:[tx+cam.dist*cp*sy,cam.dist*sp,tz+cam.dist*cp*cy],f:[-cp*sy,-sp,-cp*cy],r:[cy,0,-sy],u:[-sy*sp,cp,-cy*sp],F:Math.max(W,H)*.9};
}
function P(x,y,z){
  const d0=x-B.pos[0],d1=y-B.pos[1],d2=z-B.pos[2], zv=d0*B.f[0]+d1*B.f[1]+d2*B.f[2]; if(zv<1) return null;
  return [W/2+(d0*B.r[0]+d1*B.r[1]+d2*B.r[2])/zv*B.F, H/2-(d0*B.u[0]+d1*B.u[1]+d2*B.u[2])/zv*B.F, zv];
}
function pline(c,pts,style,w){ c.strokeStyle=style; c.lineWidth=w; c.beginPath(); let pen=false; for(const p of pts){ if(!p){ pen=false; continue; } pen?c.lineTo(p[0],p[1]):c.moveTo(p[0],p[1]); pen=true; } c.stroke(); }
function gline(c,a,b,style,w,n){ const pts=[]; n=n||(terrain?14:2); for(let i=0;i<=n;i++){ const t=i/n,x=a[0]+(b[0]-a[0])*t,z=a[1]+(b[1]-a[1])*t; pts.push(P(x,groundY(x,z)+.05,z)); } pline(c,pts,style,w); }
function gring(c,x,z,r,n,style,w){ const pts=[]; for(let i=0;i<=n;i++){ const px=x+Math.cos(i/n*TAU)*r,pz=z+Math.sin(i/n*TAU)*r; pts.push(P(px,groundY(px,pz)+.05,pz)); } pline(c,pts,style,w); }

/* textured triangle (affine map of a texture triangle onto a screen triangle) */
function tri(c,img,x0,y0,x1,y1,x2,y2,u0,v0,u1,v1,u2,v2){
  const D=u0*(v1-v2)+u1*(v2-v0)+u2*(v0-v1); if(Math.abs(D)<1e-9) return;
  const cx=(x0+x1+x2)/3,cy=(y0+y1+y2)/3,grow=(x,y)=>{ const dx=x-cx,dy=y-cy,l=Math.hypot(dx,dy)||1; return [x+dx/l*.8,y+dy/l*.8]; };
  const a=grow(x0,y0),b=grow(x1,y1),d=grow(x2,y2);
  c.save(); c.beginPath(); c.moveTo(a[0],a[1]); c.lineTo(b[0],b[1]); c.lineTo(d[0],d[1]); c.closePath(); c.clip();
  c.transform((x0*(v1-v2)+x1*(v2-v0)+x2*(v0-v1))/D,(y0*(v1-v2)+y1*(v2-v0)+y2*(v0-v1))/D,
              (x0*(u2-u1)+x1*(u0-u2)+x2*(u1-u0))/D,(y0*(u2-u1)+y1*(u0-u2)+y2*(u1-u0))/D,
              (x0*(u1*v2-u2*v1)+x1*(u2*v0-u0*v2)+x2*(u0*v1-u1*v0))/D,(y0*(u1*v2-u2*v1)+y1*(u2*v0-u0*v2)+y2*(u0*v1-u1*v0))/D);
  c.drawImage(img,0,0); c.restore();
}
function drawGround(c,lod){
  const tex=terrain&&terrTex?terrTex:baseTex, N=terrain&&HG?(lod?18:34):(lod?8:14), V=[];
  for(let j=0;j<=N;j++){ const row=[]; for(let i=0;i<=N;i++){ const x=geo.minx+i/N*geo.w,z=geo.minz+j/N*geo.d; row.push(P(x,terrain?groundY(x,z):0,z)); } V.push(row); }
  if(!tex){ const q=[V[0][0],V[0][N],V[N][N],V[N][0]]; if(q.every(Boolean)){ c.fillStyle=TH.dark?"#161d26":"#e6ebf0"; c.beginPath(); q.forEach((p,i)=>i?c.lineTo(p[0],p[1]):c.moveTo(p[0],p[1])); c.fill(); } return; }
  const cells=[];
  for(let j=0;j<N;j++) for(let i=0;i<N;i++){ const a=V[j][i],b=V[j][i+1],d=V[j+1][i+1],e=V[j+1][i]; if(!a||!b||!d||!e) continue;
    if(Math.max(a[0],b[0],d[0],e[0])<0||Math.min(a[0],b[0],d[0],e[0])>W||Math.max(a[1],b[1],d[1],e[1])<0||Math.min(a[1],b[1],d[1],e[1])>H) continue;
    cells.push({i,j,a,b,d,e,z:(a[2]+b[2]+d[2]+e[2])/4}); }
  cells.sort((p,q)=>q.z-p.z);
  const U=(i)=>i/N*TEXW, Vv=(j)=>j/N*TEXH;      // row j=0 is the northern edge = top of the texture
  for(const t of cells){ const {i,j,a,b,d,e}=t;
    tri(c,tex,a[0],a[1],b[0],b[1],d[0],d[1],U(i),Vv(j),U(i+1),Vv(j),U(i+1),Vv(j+1));
    tri(c,tex,a[0],a[1],d[0],d[1],e[0],e[1],U(i),Vv(j),U(i+1),Vv(j+1),U(i),Vv(j+1)); }
}

/* ---------------------------------------------------------------- scene (cached while the camera is still) */
const stars=Array.from({length:70},()=>({x:Math.random(),y:Math.random(),r:Math.random()*1+.2,a:Math.random()*.4+.1}));
function paintSky(c){
  const g=c.createLinearGradient(0,0,0,H); g.addColorStop(0,TH.sky0); g.addColorStop(1,TH.sky1); c.fillStyle=g; c.fillRect(0,0,W,H);
  if(TH.dark) for(const s of stars){ c.fillStyle=`rgba(190,205,225,${s.a})`; c.fillRect(s.x*W,s.y*H,s.r,s.r); }
}
function drawScene(c,lod){
  paintSky(c);
  const hy=H/2-B.F*Math.tan(cam.pitch);
  if(hy>-80&&hy<H+40){ const a=c.createLinearGradient(0,hy-70,0,hy+10); a.addColorStop(0,rgba(TH.dark?"#86a9c8":"#9db4c9",0)); a.addColorStop(1,rgba(TH.dark?"#86a9c8":"#9db4c9",TH.dark?.18:.3)); c.fillStyle=a; c.fillRect(0,hy-70,W,80); }
  drawGround(c,lod);
  const q=[[geo.minx,geo.minz],[geo.maxx,geo.minz],[geo.maxx,geo.maxz],[geo.minx,geo.maxz]];
  for(let i=0;i<4;i++) gline(c,q[i],q[(i+1)%4],rgba(TH.dark?"#9fb4c9":"#2b3a4a",.55),1.2,terrain?20:2);
  c.font="10px Inter,system-ui,sans-serif"; c.textAlign="center";
  const gm=TH.dark?"rgba(210,222,235,":"rgba(30,45,60,";
  if(L.grid) for(let lon=Math.ceil(geo.lonMin*4)/4;lon<=geo.lonMax;lon+=.25){ const x=(lon-geo.lon0)*geo.k, big=Math.abs(lon*2-Math.round(lon*2))<1e-6;
    gline(c,[x,geo.minz],[x,geo.maxz],gm+(big?.2:.08)+")",big?1:.7);
    if(big){ const p=P(x,groundY(x,geo.maxz),geo.maxz); if(p){ c.fillStyle=TH.muted; c.fillText(Math.abs(lon).toFixed(1)+"°W",p[0],p[1]+13); } } }
  if(L.grid) for(let lat=Math.ceil(geo.latMin*4)/4;lat<=geo.latMax;lat+=.25){ const z=(geo.lat0-lat)*110.57, big=Math.abs(lat*2-Math.round(lat*2))<1e-6;
    gline(c,[geo.minx,z],[geo.maxx,z],gm+(big?.2:.08)+")",big?1:.7);
    if(big){ const p=P(geo.minx,groundY(geo.minx,z),z); if(p){ c.textAlign="right"; c.fillStyle=TH.muted; c.fillText(lat.toFixed(1)+"°N",p[0]-6,p[1]+3); c.textAlign="center"; } } }
  const cx=selected?selected.x:geo.cx, cz=selected?selected.z:geo.cz;
  if(L.rings) for(const r of [25,50,75,100]){ if(r>geo.span*.9) break; gring(c,cx,cz,r,96,rgba(TH.accent,.4),1);
    const px=cx+r*.7071,pz=cz+r*.7071,p=P(px,groundY(px,pz),pz); if(p){ c.fillStyle=TH.muted; c.fillText(r+" km",p[0],p[1]-3); } }
}

/* ---------------------------------------------------------------- dynamic layer */
function sweepAndPings(t){
  const cx=selected?selected.x:geo.cx, cz=selected?selected.z:geo.cz, ang=(t*.55)%TAU, R=Math.min(geo.span*.8,110), N=36, da=.05, c0=P(cx,groundY(cx,cz),cz);
  const at=(a)=>{ const x=cx+Math.cos(a)*R,z=cz+Math.sin(a)*R; return P(x,groundY(x,z),z); };
  if(c0) for(let i=0;i<N;i++){ const p1=at(ang-i*da),p2=at(ang-(i+1)*da); if(!p1||!p2) continue;
    ctx.fillStyle=rgba(TH.accent,(TH.dark?.2:.22)*Math.pow(1-i/N,2)); ctx.beginPath(); ctx.moveTo(c0[0],c0[1]); ctx.lineTo(p1[0],p1[1]); ctx.lineTo(p2[0],p2[1]); ctx.fill(); }
  const e=at(ang); if(c0&&e){ ctx.strokeStyle=rgba(TH.accent,.8); ctx.lineWidth=1.3; ctx.beginPath(); ctx.moveTo(c0[0],c0[1]); ctx.lineTo(e[0],e[1]); ctx.stroke(); }
  return {ang,cx,cz,R};
}
const pingOf=(g,sw)=>{ const dx=g.x-sw.cx,dz=g.z-sw.cz; if(Math.hypot(dx,dz)>sw.R) return 0; return Math.exp(-((((sw.ang-Math.atan2(dz,dx))%TAU)+TAU)%TAU)*2.4); };
function drawGauge(g,t,sw){
  const col=TH.sc[g.status]||TH.sc.nodata, has=g.stage!=null, sev=SEV[g.status]||0, pg=pingOf(g,sw), y0=groundY(g.x,g.z), base=P(g.x,y0,g.z); if(!base) return;
  const alarm=L.alerts?alerts[g.site_no]:undefined;
  gring(ctx,g.x,g.z,1.7,28,rgba(col,has?.9:.45),has?1.6:1);
  if(!has){ ctx.fillStyle=rgba(col,.6); ctx.beginPath(); ctx.arc(base[0],base[1],2,0,TAU); ctx.fill(); hits.push({g,x:base[0],y:base[1],r:12}); return; }
  if(sev>=3||alarm){ const ph=((t*.6+g.x*.13)%1); gring(ctx,g.x,g.z,2+ph*9,36,rgba(alarm?TH.sc.moderate:col,.6*(1-ph)),1.5); }   // ground pulse
  const h=hOf(g,g.stage), top=P(g.x,y0+h,g.z); if(!top) return;
  const w=Math.max(1.6,.42*B.F/base[2]), staff=P(g.x,y0+hOf(g,levelRef(g)),g.z);   // faint gauge staff up to the major level, so the ticks read as a scale
  if(staff){ ctx.strokeStyle=rgba(TH.dark?"#9fb4c9":"#2b3a4a",.22); ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(base[0],base[1]); ctx.lineTo(staff[0],staff[1]); ctx.stroke(); }
  ctx.save(); if(TH.dark) ctx.globalCompositeOperation="lighter";
  ctx.strokeStyle=rgba(col,(TH.dark?.12:.10)+.2*pg); ctx.lineWidth=w*(TH.dark?4:3); ctx.beginPath(); ctx.moveTo(base[0],base[1]); ctx.lineTo(top[0],top[1]); ctx.stroke();
  const gr=ctx.createLinearGradient(base[0],base[1],top[0],top[1]); gr.addColorStop(0,rgba(col,.15)); gr.addColorStop(1,rgba(col,TH.dark?.9:.95));
  ctx.strokeStyle=gr; ctx.lineWidth=w; ctx.stroke(); ctx.restore();
  for(const c of CATS){ const lv=g.cats&&g.cats[c]; if(lv==null) continue;                  // official stages as ticks on the pillar
    const hh=y0+hOf(g,lv), a=P(g.x-B.r[0]*1.1,hh,g.z-B.r[2]*1.1), b=P(g.x+B.r[0]*1.1,hh,g.z+B.r[2]*1.1);
    if(a&&b){ ctx.strokeStyle=rgba(TH.sc[c],g.stage>=lv?1:.55); ctx.lineWidth=g.stage>=lv?2:1; ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke(); } }
  const or=Math.max(3,.9*B.F/top[2]*(1+.25*pg)+(sev>=3?Math.sin(t*4)*.6:0));
  if(TH.dark){ ctx.save(); ctx.globalCompositeOperation="lighter"; const og=ctx.createRadialGradient(top[0],top[1],0,top[0],top[1],or*3); og.addColorStop(0,rgba(col,.85)); og.addColorStop(.3,rgba(col,.35)); og.addColorStop(1,rgba(col,0));
    ctx.fillStyle=og; ctx.beginPath(); ctx.arc(top[0],top[1],or*3,0,TAU); ctx.fill(); ctx.restore(); ctx.fillStyle="#e8eef5"; ctx.beginPath(); ctx.arc(top[0],top[1],Math.max(1.4,or*.4),0,TAU); ctx.fill(); }
  else { ctx.fillStyle=rgba(col,.2); ctx.beginPath(); ctx.arc(top[0],top[1],or*2.2,0,TAU); ctx.fill(); ctx.fillStyle=col; ctx.strokeStyle="#fff"; ctx.lineWidth=1.5; ctx.beginPath(); ctx.arc(top[0],top[1],Math.max(2.5,or*.8),0,TAU); ctx.fill(); ctx.stroke(); }
  if(alarm){ const sky=P(g.x,y0+h+geo.hmax*.9,g.z); if(sky){ ctx.setLineDash([4,4]); ctx.strokeStyle=rgba(TH.sc.moderate,.9); ctx.lineWidth=1.3; ctx.beginPath(); ctx.moveTo(top[0],top[1]); ctx.lineTo(sky[0],sky[1]); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle=TH.sc.moderate; ctx.font="600 10px Inter,system-ui,sans-serif"; ctx.textAlign="center"; ctx.fillText("ALERT "+String(alarm).toUpperCase(),sky[0],sky[1]-6); } }
  if(g===selected){ ctx.strokeStyle=TH.ink; ctx.lineWidth=1.5; ctx.beginPath(); ctx.arc(top[0],top[1],or*2.6,0,TAU); ctx.stroke(); }
  if(L.labels&&(g===selected||g===hover||sev>=3||cam.dist<geo.span*1.1)){ ctx.font="11px Inter,system-ui,sans-serif"; ctx.textAlign="left"; ctx.lineWidth=3; ctx.strokeStyle=TH.dark?"rgba(8,12,17,.85)":"rgba(255,255,255,.9)"; ctx.strokeText(g.lid,top[0]+or*2.3,top[1]+4); ctx.fillStyle=TH.ink; ctx.fillText(g.lid,top[0]+or*2.3,top[1]+4); }
  hits.push({g,x:top[0],y:top[1],r:Math.max(12,or*2.2)});
}
function drawHud(){
  const cx=W-326,cy=78; ctx.save(); ctx.translate(cx,cy); ctx.strokeStyle=TH.muted; ctx.lineWidth=1; ctx.beginPath(); ctx.arc(0,0,22,0,TAU); ctx.stroke();
  ctx.rotate(cam.yaw); ctx.fillStyle=TH.sc.moderate; ctx.beginPath(); ctx.moveTo(0,-20); ctx.lineTo(4,0); ctx.lineTo(-4,0); ctx.fill(); ctx.fillStyle=TH.muted; ctx.beginPath(); ctx.moveTo(0,20); ctx.lineTo(4,0); ctx.lineTo(-4,0); ctx.fill();
  ctx.rotate(-cam.yaw); ctx.fillStyle=TH.ink; ctx.font="10px Inter,system-ui,sans-serif"; ctx.textAlign="center"; ctx.fillText("N",Math.sin(cam.yaw)*32,-Math.cos(cam.yaw)*32+3); ctx.restore();
  const kmPx=cam.dist/B.F, nice=[5,10,25,50,100,200].find(n=>n/kmPx>70)||200, len=nice/kmPx, bx=W-310-len, by=H-18;
  ctx.strokeStyle=TH.ink; ctx.lineWidth=1.5; ctx.beginPath(); ctx.moveTo(bx,by-5); ctx.lineTo(bx,by); ctx.lineTo(bx+len,by); ctx.lineTo(bx+len,by-5); ctx.stroke();
  ctx.fillStyle=TH.muted; ctx.font="10px Inter,system-ui,sans-serif"; ctx.textAlign="center"; ctx.fillText(nice+" km at centre",bx+len/2,by-8);
  ctx.textAlign="right"; ctx.fillText("Basemap: Esri, HERE, Garmin, © OpenStreetMap contributors"+(terrain?" · elevation: AWS Terrain Tiles, relief ×"+EXAG:""),W-310,H-4);
}
function globeFrame(now,dt){
  const t=now/1000; Globe.step(dt,interacting); ctx.setTransform(dpr,0,0,dpr,0,0); paintSky(ctx);
  gInfo=Globe.draw(ctx,W,H,t,TH,regions,selRegion,L,W>1100?(228+(W-294))/2:W/2,(interacting||Globe.st.fly)?.38:.6);
  updateHud(now); setViewButtons(); applyFade(dt);
}
function frame(now){
  raf=requestAnimationFrame(frame); const dt=Math.min(.25,(now-lastT)/1000); lastT=now;
  if(!W||!H) return;
  if(view==="globe"){ globeFrame(now,dt); return; }
  if(!geo){ ctx.setTransform(dpr,0,0,dpr,0,0); ctx.fillStyle=TH.sky0; ctx.fillRect(0,0,W,H); ctx.fillStyle=TH.muted; ctx.font="13px Inter,system-ui,sans-serif"; ctx.textAlign="center"; ctx.fillText(note||"loading gauges…",W/2,H/2); updateHud(now); applyFade(dt); return; }
  if(cam.auto) cam.yaw+=dt*.05;
  basis(); const lod=(interacting||cam.auto)?1:0;
  const key=[cam.yaw.toFixed(3),cam.pitch.toFixed(3),cam.dist.toFixed(1),cam.tx.toFixed(1),cam.tz.toFixed(1),W,H,terrain,texVer,TH.dark,selected&&selected.site_no,lod,L.grid,L.rings].join("|");
  if(key!==cacheKey){ sctx.setTransform(dpr,0,0,dpr,0,0); drawScene(sctx,lod); cacheKey=key; }
  ctx.setTransform(1,0,0,1,0,0); ctx.drawImage(scene,0,0); ctx.setTransform(dpr,0,0,dpr,0,0);
  hits=[]; const t=now/1000, sw=L.sweep?sweepAndPings(t):{ang:0,cx:0,cz:0,R:0};
  if(L.gauges) for(const o of G.filter(g=>g.lat!=null).map(g=>({g,z:(P(g.x,0,g.z)||[0,0,1e9])[2]})).sort((a,b)=>b.z-a.z)) drawGauge(o.g,t,sw);
  drawHud(); updateHud(now); setViewButtons(); applyFade(dt);
}
function resize(){ const r=root.getBoundingClientRect(); dpr=Math.min(2,window.devicePixelRatio||1); W=Math.round(r.width); H=Math.round(r.height);
  for(const c of [cv,scene]){ c.width=W*dpr; c.height=H*dpr; } ctx.setTransform(dpr,0,0,dpr,0,0); cacheKey=""; }

/* ---------------------------------------------------------------- data + panels */
const ageTxt=s=>s==null?"":(s<90?Math.round(s)+" s ago":(s<5400?Math.round(s/60)+" min ago":(s/3600).toFixed(1)+" h ago"));
const STATUS_LABEL={normal:"NORMAL",action:"WATCH",minor:"WARNING",moderate:"SEVERE",major:"CRITICAL",stale:"NORMAL",nodata:"NORMAL"};
async function pollRegions(){
  try{
    const d=await (await fetch("/api/regions?mode="+mode)).json(); regions=d.regions||[];
    if(regions.length){
      if(!selRegion){ selRegion=regions[0]; regionId=selRegion.id; if(!initGlobe){ initGlobe=true; Globe.setView(selRegion.lon,selRegion.lat*.8,1); } }
      else selRegion=regions.find(r=>r.id===selRegion.id)||regions[0];
    }
    fillRegionSelect(); if(view==="globe"){ drawList(); drawCard(); }
  }catch(e){}
}
function fillRegionSelect(){ const s=$("m-region"), sig=regions.map(r=>r.id).join(); if(s.dataset.sig!==sig){ s.innerHTML=regions.map(r=>`<option value="${r.id}">${r.name}</option>`).join(""); s.dataset.sig=sig; } if(selRegion) s.value=selRegion.id; }
async function pollGauges(){
  if(!regionId) return;
  try{
    const r=await fetch("/api/gauges?mode="+mode+"&region="+encodeURIComponent(regionId)); if(!r.ok){ note="console server answered "+r.status+": restart python -m dashboard.flow_monitor"; return; }
    const d=await r.json(); if(!d.locations){ note="no gauge locations yet: run python -m tools.gauge_locations"; return; }
    const gs=d.gauges.filter(g=>g.lat!=null); if(!gs.length){ note="no gauges"; return; }
    if(!geo) buildGeo(gs); else gs.forEach(g=>{ g.x=(g.lon-geo.lon0)*geo.k; g.z=(geo.lat0-g.lat)*110.57; });
    G=gs; if(selected) selected=G.find(g=>g.site_no===selected.site_no)||null;
    $("map-note").textContent=note; if(view==="region"){ drawList(); drawCard(); }
  }catch(e){ note="cannot reach the console server"; $("map-note").textContent=note; }
}
async function pollAlerts(){
  try{ const d=await (await fetch("/api/advisories")).json(); if(!d.ok) return; const a={}, want=mode==="replay"?"SIMULATION":"LIVE";
    for(const x of d.advisories){ if(x.mode===want&&["draft","approved","sent"].includes(x.status)&&x.alert_status!=="retracted"&&x.alert_status!=="rejected") a[x.site_no]=x.category; }
    alerts=a; }catch(e){}
}
function drawList(){
  list.innerHTML="";
  if(view==="globe"){
    regions.forEach(r=>{ const d=document.createElement("div"); d.className="grow"+(selRegion&&r.id===selRegion.id?" sel":"");
      d.innerHTML=`<span class="gdot" style="background:${TH.sc[r.worst]||TH.sc.nodata}"></span><div><div class="nm"></div><div class="sub"></div></div>`;
      d.querySelector(".nm").textContent=r.name; d.querySelector(".sub").textContent=`${r.n_reporting}/${r.n_gauges} gauges · ${STATUS_LABEL[r.worst]||"NORMAL"}`;
      d.onclick=()=>{ selRegion=r; $("m-region").value=r.id; Globe.fly(r.lon,r.lat*.8,Globe.st.zoom,1000); drawList(); drawCard(); }; list.appendChild(d); });
    if(!regions.length) list.innerHTML='<div class="note" style="padding:10px">no monitored areas: add one to config/regions.json</div>';
    return;
  }
  G.slice().sort((a,b)=>(SEV[b.status]-SEV[a.status])||a.name.localeCompare(b.name)).forEach(g=>{
    const d=document.createElement("div"); d.className="grow"+(g===selected?" sel":"");
    d.innerHTML=`<span class="gdot" style="background:${TH.sc[g.status]}"></span><div><div class="nm"></div><div class="sub"></div></div>`;
    d.querySelector(".nm").textContent=g.name;
    d.querySelector(".sub").textContent=(g.stage==null?"no reading":g.stage.toFixed(2)+" ft")+(g.age_s!=null?" · "+ageTxt(g.age_s):"")+" · "+g.status;
    d.onclick=()=>select(g); list.appendChild(d);
  });
}
function drawCard(){
  if(view==="globe"){ const r=selRegion;
    if(!r){ card.innerHTML='<div class="sub">GLOBE</div><div class="nm">no monitored areas yet</div>'; return; }
    card.innerHTML=`<div class="sub">MONITORED AREA</div><div class="nm" style="margin:2px 0 2px"></div><div class="sub" style="margin-bottom:8px"></div>`+
      `<span class="pill">${r.n_gauges} gauges</span><span class="pill">${r.n_reporting} reporting</span><span class="pill" style="color:${TH.sc[r.worst]};border-color:${TH.sc[r.worst]}">${STATUS_LABEL[r.worst]||"NORMAL"}</span>`+
      `<div style="margin-top:10px"><button class="btn go" id="m-enter">Enter region</button></div><div class="note" style="margin-top:8px">drag to rotate · wheel to zoom · double-click a marker to fly in</div>`;
    card.querySelector(".nm").textContent=r.name; card.querySelectorAll(".sub")[1].textContent=r.provider||"";
    card.querySelector("#m-enter").onclick=()=>enterRegion(r); return; }
  const g=selected;
  if(!g){ const c={}; G.forEach(x=>c[x.status]=(c[x.status]||0)+1);
    card.innerHTML=`<div class="sub">NETWORK</div><div class="nm" style="margin:2px 0 8px">${G.length} gauges · ${mode==="replay"?"SIMULATION replay":"live"}</div>`+
      Object.keys(SEV).filter(k=>c[k]).map(k=>`<span class="pill" style="color:${TH.sc[k]};border-color:${TH.sc[k]}">${c[k]} ${k}</span>`).join("")+
      `<div class="note" style="margin-top:8px">drag to orbit · shift-drag to pan · wheel to zoom · click a pillar<br>pillar height = stage vs that gauge's own official major level; ticks mark the official stages</div>`; return; }
  const cs=g.cats||{}, top=Math.max(cs.major||0,cs.moderate||0,g.stage||0)*1.08||10, pct=v=>Math.min(100,Math.max(0,v/top*100));
  const ticks=CATS.filter(c=>cs[c]!=null).map(c=>`<u style="left:${pct(cs[c])}%;color:${TH.sc[c]}">${c.slice(0,3)} ${cs[c]}</u>`).join("");
  card.innerHTML=`<div class="sub">${g.lid} · ${g.site_no}${g.county?" · "+g.county+" Co.":""}</div><div class="nm">${g.name}</div>`+
    `<div class="val" style="color:${TH.sc[g.status]}">${g.stage==null?"—":g.stage.toFixed(2)}<small> ft</small></div>`+
    `<div class="sub" style="color:${TH.sc[g.status]}">${g.status.toUpperCase()}${g.provisional?" · provisional":""}${g.age_s!=null?" · "+ageTxt(g.age_s):""}</div>`+
    `<div class="lvl">${g.stage!=null?`<i style="left:calc(${pct(g.stage)}% - 1px)"></i>`:""}${ticks}</div><div style="height:12px"></div>`+
    (alerts[g.site_no]?`<div class="pill" style="color:${TH.sc.moderate};border-color:${TH.sc.moderate}">open alert: ${alerts[g.site_no]}</div>`:"");
}
function select(g){ selected=g; cacheKey=""; drawList(); drawCard(); }

/* ---------------------------------------------------------------- HUD: status, scene brief, coordinates */
const fmtLat=v=>Math.abs(v).toFixed(2)+"°"+(v>=0?"N":"S"), fmtLon=v=>Math.abs(v).toFixed(2)+"°"+(v>=0?"E":"W");
const esc=t=>String(t??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
function updateHud(now){
  if(now-lastHud<400) return; lastHud=now;
  const iso=new Date().toISOString(); $("m-clock").textContent=iso.slice(0,10)+" "+iso.slice(11,19)+" UTC";
  let worst="normal", nG=0, nAl=0, brief="";
  if(view==="region"){
    for(const g of G) if(SEV[g.status]>SEV[worst]) worst=g.status;
    nG=G.length; nAl=Object.keys(alerts).length;
    const rep=G.filter(g=>g.stage!=null).length, hot=G.filter(g=>(SEV[g.status]||0)>=3).sort((a,b)=>SEV[b.status]-SEV[a.status]);
    brief=`<span class="k">Area</span>${esc(selRegion?selRegion.name:"")} · ${mode==="replay"?"SIMULATION replay":"live"}`+
      `<span class="k">Gauges</span>${rep}/${G.length} reporting`+
      `<span class="k">At or above minor flood</span>`+(hot.length?hot.slice(0,3).map(g=>`${esc(g.name)}: ${g.stage.toFixed(1)} ft (${g.status}; minor ${g.cats&&g.cats.minor!=null?g.cats.minor:"?"} ft)`).join("<br>")+(hot.length>3?`<br>+${hot.length-3} more`:""):"none")+
      `<span class="k">Open alerts</span>${nAl?Object.entries(alerts).map(([s,c])=>esc((G.find(g=>g.site_no===s)||{}).lid||s)+" "+esc(c)).join(", "):"none"}`+
      `<span class="k">Data as of</span>${iso.slice(11,16)} UTC`+(rep===0&&mode==="replay"?"<br>no replay readings seen yet: start a replay":"");
  } else {
    for(const r of regions) if(SEV[r.worst]>SEV[worst]) worst=r.worst;
    nG=regions.reduce((a,r)=>a+r.n_gauges,0);
    brief=`<span class="k">Mode</span>${mode==="replay"?"SIMULATION replay":"live"}`+regions.map(r=>`<span class="k">${esc(r.name)}</span>${r.n_reporting}/${r.n_gauges} gauges reporting<br>${r.n_flooding?r.n_flooding+" at or above minor flood":"none at or above minor flood"}`).join("")+
      `<span class="k">Data as of</span>${iso.slice(11,16)} UTC`;
  }
  const st=$("m-status"); st.textContent=STATUS_LABEL[worst]||"NORMAL"; st.style.color=TH.sc[worst==="stale"||worst==="nodata"?"normal":worst];
  $("n-gauges").textContent=nG||""; $("n-alerts").textContent=view==="region"?String(nAl):"";
  if($("m-brief").dataset.h!==brief){ $("m-brief").innerHTML=brief; $("m-brief").dataset.h=brief; }
  let co;
  if(view==="globe"){ const sd=Globe.sunDeg(); const ll=gInfo&&Globe.fromScreen(ptr.x,ptr.y,gInfo.cx,gInfo.cy,gInfo.R);
    co=`${ll?"CURSOR "+fmtLat(ll[1]*180/Math.PI)+" "+fmtLon(wrapDeg(ll[0]*180/Math.PI)):"CURSOR —"} · ZOOM ×${Globe.st.zoom.toFixed(1)} · SUB-SOLAR ${fmtLat(sd.lat)} ${fmtLon(wrapDeg(sd.lon))}`; }
  else if(geo) co=`CENTRE ${fmtLat(toLat(geo.cz+cam.tz))} ${fmtLon(toLon(geo.cx+cam.tx))} · ALT ${Math.round(cam.dist*Math.sin(cam.pitch))} KM · PITCH ${Math.round(cam.pitch*180/Math.PI)}°`;
  else co="";
  $("m-coords").textContent=co;
}
const wrapDeg=d=>((d+540)%360)-180;

/* ---------------------------------------------------------------- globe <-> region */
function snapshot(){ if(!fadeCv) fadeCv=document.createElement("canvas"); fadeCv.width=cv.width; fadeCv.height=cv.height; fadeCv.getContext("2d").drawImage(cv,0,0); fadeA=1; }
function applyFade(dt){ if(fadeA<=0||!fadeCv) return; ctx.setTransform(1,0,0,1,0,0); ctx.globalAlpha=fadeA; ctx.drawImage(fadeCv,0,0); ctx.globalAlpha=1; ctx.setTransform(dpr,0,0,dpr,0,0); fadeA=Math.max(0,fadeA-dt*1.8); }
function enterRegion(r,opts){
  if(!r||flying) return; opts=opts||{}; selRegion=r;
  if(regionId!==r.id||!geo){ regionId=r.id; geo=null; G=[]; alerts={}; selected=null; }
  $("m-region").value=r.id; pollGauges(); pollAlerts();
  const go=()=>{ snapshot(); view="region"; flying=false; cam.auto=false; if(opts.pitch!=null){ cam.pitch=opts.pitch; if(opts.pitch>1.5) cam.yaw=0; } cacheKey=""; syncAuto(); drawList(); drawCard(); };
  if(view==="globe"){ flying=true; Globe.fly(r.lon,r.lat,7,1500,go); } else go();
}
function exitRegion(){
  if(view==="globe"||flying||!selRegion) return; const r=selRegion; snapshot(); view="globe"; Globe.setView(r.lon,r.lat,7); flying=true;
  Globe.fly(r.lon,r.lat*.8,1,1500,()=>{ flying=false; }); syncAuto(); drawList(); drawCard();
}

/* ---------------------------------------------------------------- input */
let drag=null, ptr={x:0,y:0}, wheelT=0;
const regionAt=(mx,my)=>{ let best=null,bd=1e9; for(const h of Globe.hits()){ const d=Math.hypot(h.x-mx,h.y-my); if(d<h.r&&d<bd){ best=h; bd=d; } } return best&&best.region; };
cv.addEventListener("contextmenu",e=>e.preventDefault());
cv.addEventListener("pointerdown",e=>{ cv.setPointerCapture(e.pointerId); drag={x:e.clientX,y:e.clientY,moved:0,pan:e.shiftKey||e.button===2}; cv.classList.add("drag"); interacting=true; });
cv.addEventListener("pointermove",e=>{
  const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top; ptr={x:mx,y:my};
  if(drag){ const dx=e.clientX-drag.x,dy=e.clientY-drag.y; drag.moved+=Math.abs(dx)+Math.abs(dy); drag.x=e.clientX; drag.y=e.clientY; tip.hidden=true;
    if(view==="globe"){ Globe.drag(dx,dy,W,H); syncAuto(); return; }
    if(drag.pan){ const k=cam.dist/B.F, kf=k/Math.max(.2,Math.sin(cam.pitch)); cam.tx-=dx*Math.cos(cam.yaw)*k+dy*Math.sin(cam.yaw)*kf; cam.tz+=dx*Math.sin(cam.yaw)*k-dy*Math.cos(cam.yaw)*kf; }
    else{ cam.yaw-=dx*.006; cam.pitch=Math.max(.12,Math.min(1.5708,cam.pitch+dy*.005)); cam.auto=false; syncAuto(); } return; }
  if(view==="globe"){ const rg=regionAt(mx,my); cv.style.cursor=rg?"pointer":"grab"; if(rg){ tip.hidden=false; tip.style.left=mx+"px"; tip.style.top=my+"px"; tip.textContent=`${rg.name} · ${rg.n_reporting}/${rg.n_gauges} gauges · ${STATUS_LABEL[rg.worst]||"NORMAL"}`; } else tip.hidden=true; return; }
  let best=null,bd=1e9; for(const h of hits){ const d=Math.hypot(h.x-mx,h.y-my); if(d<h.r&&d<bd){ best=h; bd=d; } }
  hover=best?best.g:null; cv.style.cursor=best?"pointer":"grab";
  if(best){ tip.hidden=false; tip.style.left=mx+"px"; tip.style.top=my+"px"; tip.textContent=`${best.g.name} · ${best.g.stage==null?"no reading":best.g.stage.toFixed(2)+" ft"} · ${best.g.status}`; } else tip.hidden=true;
});
cv.addEventListener("pointerup",e=>{ cv.classList.remove("drag"); interacting=false;
  if(drag&&drag.moved<5){ const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
    if(view==="globe"){ const rg=regionAt(mx,my); if(rg){ selRegion=rg; $("m-region").value=rg.id; drawList(); drawCard(); } }
    else { let best=null,bd=1e9; for(const h of hits){ const d=Math.hypot(h.x-mx,h.y-my); if(d<h.r&&d<bd){ best=h; bd=d; } } select(best?best.g:null); } }
  drag=null; });
cv.addEventListener("pointerleave",()=>{ hover=null; tip.hidden=true; });
cv.addEventListener("wheel",e=>{ e.preventDefault(); interacting=true; clearTimeout(wheelT); wheelT=setTimeout(()=>interacting=false,250);
  if(view==="globe"){ Globe.zoom(e.deltaY); return; } if(!geo) return; cam.dist=Math.max(geo.span*.25,Math.min(geo.span*5,cam.dist*Math.exp(e.deltaY*.0012))); },{passive:false});
cv.addEventListener("dblclick",()=>{ if(view==="globe"){ const rg=regionAt(ptr.x,ptr.y)||selRegion; if(rg) enterRegion(rg); } else if(geo){ cam.dist=geo.span*1.55; cam.tx=cam.tz=0; } });
const autoOn=()=>view==="globe"?Globe.st.auto:cam.auto;
function syncAuto(){ $("m-auto").setAttribute("aria-pressed",autoOn()); }
let lastBtn="";
function setViewButtons(){ const k=view+(cam.pitch>1.45); if(k===lastBtn) return; lastBtn=k; const g=view==="globe", flat=cam.pitch>1.45;
  $("v-globe").setAttribute("aria-pressed",g); $("v-2d").setAttribute("aria-pressed",!g&&flat); $("v-3d").setAttribute("aria-pressed",!g&&!flat); $("m-terrain").disabled=g; }
function setSeg(live){ $("m-live").setAttribute("aria-pressed",live); $("m-replay").setAttribute("aria-pressed",!live); }
$("m-auto").onclick=()=>{ if(view==="globe") Globe.st.auto=!Globe.st.auto; else cam.auto=!cam.auto; syncAuto(); };
$("v-globe").onclick=()=>exitRegion();
$("v-2d").onclick=()=>{ if(view==="globe") enterRegion(selRegion,{pitch:1.5708}); else { cam.pitch=1.5708; cam.yaw=0; cam.auto=false; syncAuto(); } };
$("v-3d").onclick=()=>{ if(view==="globe") enterRegion(selRegion,{pitch:.62}); else cam.pitch=.62; };
const switchMode=m=>{ mode=m; setSeg(m==="live"); geo=null; G=[]; alerts={}; pollRegions(); pollGauges(); pollAlerts(); };
$("m-live").onclick=()=>switchMode("live");
$("m-replay").onclick=()=>switchMode("replay");
$("m-terrain").onchange=e=>{ terrain=e.target.checked; cacheKey=""; if(terrain&&geo) loadDem(); else note=""; $("map-note").textContent=note; };
$("m-region").onchange=e=>{ const r=regions.find(x=>x.id===e.target.value); if(!r) return; selRegion=r; if(view==="globe"){ Globe.fly(r.lon,r.lat*.8,Globe.st.zoom,1100); drawList(); drawCard(); } else enterRegion(r); };
document.querySelectorAll("[data-layer]").forEach(cb=>{ cb.onchange=()=>{ L[cb.dataset.layer]=cb.checked; cacheKey=""; }; });
$("m-presets").addEventListener("click",e=>{ const b=e.target.closest("button"); if(!b) return; for(const x of $("m-presets").children) x.setAttribute("aria-pressed",x===b);
  cv.className=cv.className.replace(/\bvp-\w+/g,"").trim(); if(b.dataset.preset!=="standard") cv.classList.add("vp-"+b.dataset.preset); });
document.addEventListener("themechange",()=>{ readTheme(); cacheKey=""; if(geo){ loadBase(); if(dem) composeTerrain(); } drawList(); drawCard(); });

window.Map3D={
  show(){ if(running) return; running=true; resize(); lastT=performance.now(); raf=requestAnimationFrame(frame);
    pollRegions().then(()=>{ pollGauges(); pollAlerts(); });
    pollA=setInterval(()=>{ pollRegions(); if(view==="region"||flying) pollGauges(); },4000); pollB=setInterval(pollAlerts,6000); },
  hide(){ running=false; cancelAnimationFrame(raf); clearInterval(pollA); clearInterval(pollB); }
};
if(!$("tab-map").hidden) window.Map3D.show();       // the page script may have opened this tab before we loaded
if(window.ResizeObserver) new ResizeObserver(()=>{ if(running) resize(); }).observe(root); else addEventListener("resize",()=>{ if(running) resize(); });
})();
