/* Map view: one full-screen canvas, also the landing page's background. Globe (globe.js) <-> flat world map, both showing every
   monitored area: the flat map zooms out to the whole world and in to a single gauge; region markers above about 1400 km, gauges below.
   Ground  = world texture plus a sharper patch for the area in view, re-tiled when the camera settles (Esri gray canvas or
             satellite; the bundled NASA image when tiles cannot be reached). Terrain adds real elevation (AWS Terrain Tiles, decoded in
             the browser) to the patch at any zoom, with hillshade and contours; the relief scale follows the area in view.
   Gauges  = pillars: height is the newest stage against THAT gauge's own official NWS major level, with ticks at the official
             action/minor/moderate/major stages. A radar sweep pings gauges, ground rings pulse at minor flood or above,
             a dashed red beam marks an open alert (only alerts of the mode on screen: live or SIMULATION replay).
   Rain    = a disc of the real 10 km circle round each gauge, stronger with more rain in the last 3 h (MRMS mean depth, /api/rain).
   Data    = /api/regions, /api/gauges (stage, status, official levels), /api/advisories (alerts). No library: own perspective camera. */
(function(){
"use strict";
const $=id=>document.getElementById(id);
const root=$("map3d"), cv=$("m-canvas"), ctx=cv.getContext("2d");
const scene=document.createElement("canvas"), sctx=scene.getContext("2d");
const card=$("m-card"), list=$("m-list"), tip=$("m-tip");
const SEV={major:5,moderate:4,minor:3,action:2,normal:1,stale:0,nodata:0}, CATS=["action","minor","moderate","major"], TAU=Math.PI*2, FLAT=Math.PI/2, TILT=.62, D2R=Math.PI/180;
const FAR_KM=1400, DIST_MIN=12, DIST_MAX=36000;
let TH=null;
function readTheme(){
  const cs=getComputedStyle(document.documentElement), v=n=>cs.getPropertyValue(n).trim();
  TH={dark:document.documentElement.dataset.theme!=="light",ink:v("--ink"),muted:v("--muted"),accent:v("--accent"),rain:v("--rain"),sky0:v("--sky0"),sky1:v("--sky1"),
      sc:Object.fromEntries(["normal","action","minor","moderate","major","stale","nodata"].map(k=>[k,v("--s-"+k)]))};
}
readTheme();
const rgba=(hex,a)=>{ const n=parseInt(hex.slice(1),16); return `rgba(${n>>16},${(n>>8)&255},${n&255},${a})`; };
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));

let W=0,H=0,dpr=1,running=false,raf=0,lastT=performance.now();
const cam={yaw:0,pitch:FLAT,dist:400,tx:0,tz:0,auto:false};
let G=[],mode="live",selected=null,hover=null,alerts={},hits=[],geo=null,note="",pollA=0,pollB=0,terrain=false,imagery=false,interacting=false;
let cacheKey="",texVer=0,camFly=null,lastFar=null;
let scrub=null, bundledBase=false;
let view="globe",regions=[],selRegion=null,initGlobe=false,fadeCv=null,fadeA=0,flying=false,lastHud=0,gInfo=null,land=0,landTo=0,landDur=1.9;
let rain={};
const L={gauges:true,alerts:true,rain:true,sweep:true,rings:true,grid:true,labels:true,regions:true};   // data layers (panel on the left)
const F=()=>Math.max(W,H)*.9, isFar=()=>cam.dist>FAR_KM;

/* ---------------------------------------------------------------- geometry: one plane for the whole world (equirectangular, true scale near the first region) */
function ensureGeo(){
  if(geo||!regions.length) return;
  const r=regions[0], lat0=r.lat, lon0=r.lon, k=111.32*Math.cos(lat0*D2R);
  geo={lat0,lon0,k,cx:0,cz:0,hmax:50,z:0,world:{minx:(-180-lon0)*k,maxx:(180-lon0)*k,minz:(lat0-85)*110.57,maxz:(lat0+85)*110.57}};
  G.forEach(place); loadWorld();
}
function place(g){ g.x=(g.lon-geo.lon0)*geo.k; g.z=(geo.lat0-g.lat)*110.57; }
const toLon=x=>geo.lon0+x/geo.k, toLat=z=>geo.lat0-z/110.57;     // z grows southwards
function levelRef(g){ const c=g.cats||{}; return c.major||c.moderate||(c.minor?c.minor*1.4:null)||(c.action?c.action*1.8:20); }
const hOf=(g,s)=>Math.max(0,Math.min(1.35,s/levelRef(g)))*geo.hmax;
const clampTarget=()=>{ cam.tx=clamp(cam.tx,geo.world.minx,geo.world.maxx); cam.tz=clamp(cam.tz,geo.world.minz,geo.world.maxz); };

/* ---------------------------------------------------------------- tiles, elevation, textures */
const lon2x=(lon,z)=>(lon+180)/360*Math.pow(2,z);
const lat2y=(lat,z)=>{ const s=Math.sin(lat*D2R); return (.5-Math.log((1+s)/(1-s))/(4*Math.PI))*Math.pow(2,z); };
const imgCache=new Map();
function getImg(u){ if(!imgCache.has(u)) imgCache.set(u,new Promise(res=>{ const im=new Image(); im.crossOrigin="anonymous"; im.onload=()=>res(im); im.onerror=()=>res(null); im.src=u; })); return imgCache.get(u); }
function bounds(minx,maxx,minz,maxz,z){ return {minx,maxx,minz,maxz,z,w:maxx-minx,d:maxz-minz,lonMin:toLon(minx),lonMax:toLon(maxx),latMax:toLat(minz),latMin:toLat(maxz)}; }
function tileRange(b,z){ const n=(1<<z)-1; return {z,x0:clamp(Math.floor(lon2x(b.lonMin,z)),0,n),x1:clamp(Math.floor(lon2x(b.lonMax,z)),0,n),y0:clamp(Math.floor(lat2y(b.latMax,z)),0,n),y1:clamp(Math.floor(lat2y(b.latMin,z)),0,n)}; }
async function mosaic(R,urlOf){
  const nx=R.x1-R.x0+1, ny=R.y1-R.y0+1, jobs=[];
  for(let y=R.y0;y<=R.y1;y++) for(let x=R.x0;x<=R.x1;x++) jobs.push(getImg(urlOf(R.z,x,y)).then(im=>({im,x,y})));
  const done=await Promise.all(jobs), c=document.createElement("canvas"); c.width=nx*256; c.height=ny*256;
  const g=c.getContext("2d"); let ok=0; for(const t of done) if(t.im){ g.drawImage(t.im,(t.x-R.x0)*256,(t.y-R.y0)*256); ok++; }
  return ok?c:null;
}
const baseUrl=()=>imagery?(z,x,y)=>`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`
  :(z,x,y)=>`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/${TH.dark?"World_Dark_Gray_Base":"World_Light_Gray_Base"}/MapServer/tile/${z}/${y}/${x}`;
function warp(m,R,b,tw,th,asPhoto){                                   // mercator tiles -> linear-latitude texture, in thin strips; tinted to the theme
  const t=document.createElement("canvas"); t.width=tw; t.height=th; const g=t.getContext("2d");
  g.fillStyle=TH.dark?"#0a1426":"#cfe8f8"; g.fillRect(0,0,tw,th);
  if(m){ const z=R.z, sx=(lon2x(b.lonMin,z)-R.x0)*256, sw=(lon2x(b.lonMax,z)-lon2x(b.lonMin,z))*256, S=4;
    for(let j=0;j<th;j+=S){ const la=b.latMax-j/th*(b.latMax-b.latMin), lb=b.latMax-(j+S)/th*(b.latMax-b.latMin);
      g.drawImage(m,sx,(lat2y(la,z)-R.y0)*256,sw,(lat2y(lb,z)-lat2y(la,z))*256,0,j,tw,S+1); }
    if(!imagery&&!asPhoto){ g.globalCompositeOperation="multiply"; g.fillStyle=TH.dark?"#8fb6f2":"#c4e6fb"; g.fillRect(0,0,tw,th); g.globalCompositeOperation="source-over"; } }
  t._b=b; return t;
}
let worldTex=null,worldVer=0,baseTex=null,terrTex=null,baseVer=0,demVer=0,dem=null,HG=null,relief=null,patchT=0;
async function loadWorld(){
  const ver=++worldVer, w=geo.world, b=bounds(w.minx,w.maxx,w.minz,w.maxz,3), R=tileRange(b,3); let m=Globe.offline?null:await mosaic(R,baseUrl()), Rm=R;
  if(!m){ m=await Globe.bundled(); Rm={z:3,x0:0,x1:7,y0:0,y1:7}; bundledBase=!!m; note=m?"no connection to the map tiles: showing the bundled world image":"basemap tiles unavailable: showing the plain ground"; }
  else bundledBase=false;
  if(ver!==worldVer) return; $("map-note").textContent=bundledBase||!m?note:"";
  worldTex=warp(m,Rm,b,2048,1024,bundledBase); texVer++;
}
function viewHalf(){ const s=Math.max(.35,Math.sin(cam.pitch)), tilt=1+(1-s)*.8; return [cam.dist*W/F()*.55*tilt,cam.dist*H/F()*.55/s]; }
function clearPatch(){ baseTex=terrTex=null; dem=null; HG=null; relief=null; demVer++; texVer++; }
function updatePatch(force){                                  // called when the camera settles: re-tile only if the view left the patch or the zoom changed
  if(!geo||view!=="region"||!W) return;
  const [vw,vd]=viewHalf(), lonSpan=2*vw/geo.k; let z=clamp(Math.round(Math.log2(W/256*360/lonSpan)),2,12);
  if(Globe.offline||bundledBase){ noteTerrain(); return; }
  if(z<=3&&!terrain){ ++baseVer; if(baseTex) clearPatch(); return; }
  z=Math.max(z,3);
  const c=baseTex&&baseTex._b, inside=c&&cam.tx-vw>=c.minx&&cam.tx+vw<=c.maxx&&cam.tz-vd>=c.minz&&cam.tz+vd<=c.maxz;
  if(!force&&c&&c.z===z&&inside) return;
  const m=1.45, w=geo.world, b=bounds(clamp(cam.tx-vw*m,w.minx,w.maxx),clamp(cam.tx+vw*m,w.minx,w.maxx),clamp(cam.tz-vd*m,w.minz,w.maxz),clamp(cam.tz+vd*m,w.minz,w.maxz),z);
  let R=tileRange(b,z); while(z>3&&(R.x1-R.x0+1)*(R.y1-R.y0+1)>70){ z--; R=tileRange(b,z); } b.z=z;
  const ver=++baseVer;
  mosaic(R,baseUrl()).then(mo=>{ if(ver!==baseVer||!mo) return;
    const tw=Math.min(2048,(R.x1-R.x0+1)*256), th=clamp(Math.round(tw*b.d/b.w),256,2048); baseTex=warp(mo,R,b,tw,th); Object.assign(geo,{minx:b.minx,maxx:b.maxx,minz:b.minz,maxz:b.maxz,w:b.w,d:b.d,lonMin:b.lonMin,lonMax:b.lonMax,latMin:b.latMin,latMax:b.latMax,z:b.z});
    terrTex=null; dem=null; HG=null; relief=null; demVer++; texVer++; if(terrain) loadDem(); });
}
function noteTerrain(){ if(terrain&&(Globe.offline||bundledBase)){ note="terrain needs the elevation tiles: no connection"; $("map-note").textContent=note; } }
async function loadDem(){
  if(!baseTex||dem) return;
  let z=Math.min(baseTex._b.z,10), R=tileRange(baseTex._b,z); while(z>2&&(R.x1-R.x0+1)*(R.y1-R.y0+1)>48){ z--; R=tileRange(baseTex._b,z); }
  const ver=++demVer; note="loading elevation…"; $("map-note").textContent=note;
  const m=await mosaic(R,(z,x,y)=>`https://s3.amazonaws.com/elevation-tiles-prod/terrarium/${z}/${x}/${y}.png`);
  if(ver!==demVer) return;
  if(!m){ note="elevation tiles unavailable (offline?): terrain not shown"; $("map-note").textContent=note; return; }
  const id=m.getContext("2d").getImageData(0,0,m.width,m.height).data, h=new Float32Array(m.width*m.height);
  for(let i=0;i<h.length;i++) h[i]=Math.max(0,id[i*4]*256+id[i*4+1]+id[i*4+2]/256-32768);   // the sea floor stays flat: only land relief counts
  dem={R,w:m.width,h:m.height,data:h}; note=""; $("map-note").textContent="";
  buildRelief(); buildHeightGrid(); composeTerrain(); texVer++;
}
function elevAt(lon,lat){
  const D=dem, x=(lon2x(lon,D.R.z)-D.R.x0)*256, y=(lat2y(lat,D.R.z)-D.R.y0)*256;
  const xi=clamp(Math.floor(x),0,D.w-2), yi=clamp(Math.floor(y),0,D.h-2), fx=clamp(x-xi,0,1), fy=clamp(y-yi,0,1), d=D.data, w=D.w;
  return (d[yi*w+xi]*(1-fx)+d[yi*w+xi+1]*fx)*(1-fy)+(d[(yi+1)*w+xi]*(1-fx)+d[(yi+1)*w+xi+1]*fx)*fy;
}
function buildHeightGrid(){
  const N=48, g=new Float32Array((N+1)*(N+1)); let e0=1e9, e1=-1e9;
  for(let j=0;j<=N;j++) for(let i=0;i<=N;i++){ const e=elevAt(toLon(geo.minx+i/N*geo.w),toLat(geo.minz+j/N*geo.d)); g[j*(N+1)+i]=e; if(e<e0) e0=e; if(e>e1) e1=e; }
  geo.exag=clamp(geo.w*.06/Math.max(.05,(e1-e0)/1000),3,250);                // the highest relief reads as about 6 % of the area in view, at every zoom
  for(let i=0;i<g.length;i++) g[i]=(g[i]-e0)/1000*geo.exag;
  HG={N,g};
}
function groundY(x,z){
  if(!terrain||!HG||x<geo.minx||x>geo.maxx||z<geo.minz||z>geo.maxz) return 0;
  const N=HG.N,u=clamp((x-geo.minx)/geo.w*N,0,N-1e-6),v=clamp((z-geo.minz)/geo.d*N,0,N-1e-6),i=Math.floor(u),j=Math.floor(v),fx=u-i,fy=v-j,g=HG.g,s=N+1;
  return (g[j*s+i]*(1-fx)+g[j*s+i+1]*fx)*(1-fy)+(g[(j+1)*s+i]*(1-fx)+g[(j+1)*s+i+1]*fx)*fy;
}
function buildRelief(){                       // hillshade + contours as a transparent overlay, computed once per DEM
  const HW=900,HHt=Math.round(HW*geo.d/geo.w), E=new Float32Array(HW*HHt);
  for(let j=0;j<HHt;j++) for(let i=0;i<HW;i++) E[j*HW+i]=elevAt(geo.lonMin+(i+.5)/HW*(geo.lonMax-geo.lonMin),geo.latMax-(j+.5)/HHt*(geo.latMax-geo.latMin));
  const cx=geo.w*1000/HW, cy=geo.d*1000/HHt, ex=clamp(cx/180,4,18), alt=40*D2R, az=315*D2R;
  const ci=[100,200,500,1000,2000].find(v=>v>=cx*2.2)||2000, cb=ci*5;        // contour spacing follows the scale, so a wide view is not a solid mass of lines
  const Ld=[Math.cos(alt)*Math.sin(az),Math.cos(alt)*Math.cos(az),Math.sin(alt)], flat=Ld[2];
  const c=document.createElement("canvas"); c.width=HW; c.height=HHt; const g=c.getContext("2d"), im=g.createImageData(HW,HHt), o=im.data;
  for(let j=1;j<HHt-1;j++) for(let i=1;i<HW-1;i++){
    const k=j*HW+i, gx=(E[k+1]-E[k-1])/(2*cx)*ex, gn=(E[k-HW]-E[k+HW])/(2*cy)*ex, inv=1/Math.sqrt(gx*gx+gn*gn+1);
    const s=(-gx*Ld[0]-gn*Ld[1]+Ld[2])*inv, d=s-flat, p=k*4;
    if(d<0){ o[p]=o[p+1]=o[p+2]=0; o[p+3]=Math.min(175,-d*560); } else { o[p]=o[p+1]=o[p+2]=255; o[p+3]=Math.min(130,d*420); }
    const a=Math.floor(E[k]/ci), b=Math.floor(E[k+1]/ci), c2=Math.floor(E[k+HW]/ci);
    if(a!==b||a!==c2){ const idx=Math.floor(E[k]/cb)!==Math.floor(E[k+1]/cb)||Math.floor(E[k]/cb)!==Math.floor(E[k+HW]/cb);
      o[p]=40;o[p+1]=48;o[p+2]=58;o[p+3]=idx?120:55; }
  }
  g.putImageData(im,0,0); relief=c;
}
function composeTerrain(){
  if(!baseTex||!dem||!relief) return;
  const t=document.createElement("canvas"); t.width=baseTex.width; t.height=baseTex.height; const g=t.getContext("2d");
  g.drawImage(baseTex,0,0); g.globalAlpha=TH.dark?.9:.8; g.drawImage(relief,0,0,t.width,t.height); t._b=baseTex._b; terrTex=t;
}

/* ---------------------------------------------------------------- camera */
let B=null;
function basis(){
  const cp=Math.cos(cam.pitch),sp=Math.sin(cam.pitch),cy=Math.cos(cam.yaw),sy=Math.sin(cam.yaw), tx=cam.tx, tz=cam.tz;
  B={pos:[tx+cam.dist*cp*sy,cam.dist*sp,tz+cam.dist*cp*cy],f:[-cp*sy,-sp,-cp*cy],r:[cy,0,-sy],u:[-sy*sp,cp,-cy*sp],F:F()};
}
function P(x,y,z){
  const d0=x-B.pos[0],d1=y-B.pos[1],d2=z-B.pos[2], zv=d0*B.f[0]+d1*B.f[1]+d2*B.f[2]; if(zv<1) return null;
  return [W/2+(d0*B.r[0]+d1*B.r[1]+d2*B.r[2])/zv*B.F, H/2-(d0*B.u[0]+d1*B.u[1]+d2*B.u[2])/zv*B.F, zv];
}
function pline(c,pts,style,w){ c.strokeStyle=style; c.lineWidth=w; c.beginPath(); let pen=false; for(const p of pts){ if(!p){ pen=false; continue; } pen?c.lineTo(p[0],p[1]):c.moveTo(p[0],p[1]); pen=true; } c.stroke(); }
function gline(c,a,b,style,w,n){ const pts=[]; n=n||(terrain?14:2); for(let i=0;i<=n;i++){ const t=i/n,x=a[0]+(b[0]-a[0])*t,z=a[1]+(b[1]-a[1])*t; pts.push(P(x,groundY(x,z)+.05,z)); } pline(c,pts,style,w); }
function gring(c,x,z,r,n,style,w){ const pts=[]; for(let i=0;i<=n;i++){ const px=x+Math.cos(i/n*TAU)*r,pz=z+Math.sin(i/n*TAU)*r; pts.push(P(px,groundY(px,pz)+.05,pz)); } pline(c,pts,style,w); }
function flyCam(tx,tz,dist,ms,done){ camFly={tx0:cam.tx,tz0:cam.tz,d0:cam.dist,tx1:tx,tz1:tz,d1:clamp(dist,DIST_MIN,DIST_MAX),t:0,dur:ms/1000,done}; }
function stepCam(dt){ if(!camFly) return; const f=camFly; f.t=Math.min(1,f.t+dt/f.dur); const e=f.t<.5?2*f.t*f.t:1-Math.pow(-2*f.t+2,2)/2;
  cam.tx=f.tx0+(f.tx1-f.tx0)*e; cam.tz=f.tz0+(f.tz1-f.tz0)*e; cam.dist=Math.exp(Math.log(f.d0)+(Math.log(f.d1)-Math.log(f.d0))*e); if(f.t>=1){ camFly=null; if(f.done) f.done(); } }

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
function drawMesh(c,tex,N,useH){
  const b=tex._b, V=[], TW=tex.width, THt=tex.height;
  for(let j=0;j<=N;j++){ const row=[]; for(let i=0;i<=N;i++){ const x=b.minx+i/N*b.w,z=b.minz+j/N*b.d; row.push(P(x,useH?groundY(x,z):0,z)); } V.push(row); }
  const cells=[];
  for(let j=0;j<N;j++) for(let i=0;i<N;i++){ const a=V[j][i],q=V[j][i+1],d=V[j+1][i+1],e=V[j+1][i]; if(!a||!q||!d||!e) continue;
    if(Math.max(a[0],q[0],d[0],e[0])<0||Math.min(a[0],q[0],d[0],e[0])>W||Math.max(a[1],q[1],d[1],e[1])<0||Math.min(a[1],q[1],d[1],e[1])>H) continue;
    cells.push({i,j,a,q,d,e,z:(a[2]+q[2]+d[2]+e[2])/4}); }
  cells.sort((p,q)=>q.z-p.z);
  const U=i=>i/N*TW, Vv=j=>j/N*THt;              // row j=0 is the northern edge = top of the texture
  for(const t of cells){ const {i,j,a,q,d,e}=t;
    tri(c,tex,a[0],a[1],q[0],q[1],d[0],d[1],U(i),Vv(j),U(i+1),Vv(j),U(i+1),Vv(j+1));
    tri(c,tex,a[0],a[1],d[0],d[1],e[0],e[1],U(i),Vv(j),U(i+1),Vv(j+1),U(i),Vv(j+1)); }
}
function drawGround(c,lod){
  if(worldTex) drawMesh(c,worldTex,lod?18:28,false);
  const tex=terrain&&terrTex?terrTex:baseTex; if(tex) drawMesh(c,tex,terrain&&HG?(lod?18:34):(lod?8:14),!!(terrain&&HG));
}

/* ---------------------------------------------------------------- sky and scene (scene cached while the camera is still) */
let starCv=null;
function buildStars(){
  starCv=document.createElement("canvas"); starCv.width=Math.max(1,W); starCv.height=Math.max(1,H); const g=starCv.getContext("2d");
  for(const [x,y,r,col] of [[.2,.25,.5,"rgba(60,90,190,.10)"],[.82,.15,.42,"rgba(110,70,180,.07)"],[.55,.8,.55,"rgba(30,100,170,.08)"]]){ const n=g.createRadialGradient(x*W,y*H,0,x*W,y*H,r*Math.max(W,H)); n.addColorStop(0,col); n.addColorStop(1,"rgba(0,0,0,0)"); g.fillStyle=n; g.fillRect(0,0,W,H); }
  const count=Math.round(W*H/1500); for(let i=0;i<count;i++){ const b=Math.pow(Math.random(),3), s=.45+b*1.4, tone=Math.random(); g.fillStyle=`rgba(${tone<.2?"255,226,200":tone<.5?"190,215,255":"235,242,255"},${.2+b*.8})`; g.beginPath(); g.arc(Math.random()*W,Math.random()*H,s*.6,0,TAU); g.fill(); }
}
function paintSky(c){
  const g=c.createLinearGradient(0,0,0,H); g.addColorStop(0,TH.sky0); g.addColorStop(1,TH.sky1); c.fillStyle=g; c.fillRect(0,0,W,H);
  if(TH.dark){ if(!starCv||starCv.width!==Math.max(1,W)||starCv.height!==Math.max(1,H)) buildStars();
    const off=view==="globe"?((Globe.st.lon*22)%W+W)%W:0; c.drawImage(starCv,-off,0); if(off) c.drawImage(starCv,W-off,0); }
  else { const s=c.createRadialGradient(W*.85,H*.1,0,W*.85,H*.1,Math.max(W,H)*.55); s.addColorStop(0,"rgba(255,255,255,.75)"); s.addColorStop(1,"rgba(255,255,255,0)"); c.fillStyle=s; c.fillRect(0,0,W,H); }
}
function drawScene(c,lod){
  paintSky(c);
  const hy=H/2-B.F*Math.tan(cam.pitch);
  if(cam.pitch<1.4&&hy>-80&&hy<H+40){ const a=c.createLinearGradient(0,hy-70,0,hy+10); a.addColorStop(0,rgba(TH.accent,0)); a.addColorStop(1,rgba(TH.accent,TH.dark?.18:.3)); c.fillStyle=a; c.fillRect(0,hy-70,W,80); }
  drawGround(c,lod);
  c.font="10px Inter,system-ui,sans-serif"; c.textAlign="center";
  const gm=TH.dark?"rgba(190,215,250,":"rgba(15,60,105,";
  if(L.grid){ const [vw,vd]=viewHalf(), step=[.25,.5,1,2,5,10,30].find(d=>d*110>cam.dist*.1)||30, x0=cam.tx-vw*1.3,x1=cam.tx+vw*1.3,z0=cam.tz-vd*1.3,z1=cam.tz+vd*1.3, big=v=>Math.abs(v/(2*step)-Math.round(v/(2*step)))<1e-6;
    const lo0=Math.max(-180,toLon(x0)),lo1=Math.min(180,toLon(x1)),la0=Math.max(-85,toLat(z1)),la1=Math.min(85,toLat(z0));
    for(let lon=Math.ceil(lo0/step)*step;lon<=lo1;lon+=step){ const x=(lon-geo.lon0)*geo.k,b=big(lon); gline(c,[x,z0],[x,z1],gm+(b?.2:.08)+")",b?1:.7);
      if(b){ const p=P(x,0,cam.tz+vd*.8); if(p) labelAt(c,fmtLon(lon),p[0],p[1]); } }
    for(let lat=Math.ceil(la0/step)*step;lat<=la1;lat+=step){ const z=(geo.lat0-lat)*110.57,b=big(lat); gline(c,[x0,z],[x1,z],gm+(b?.2:.08)+")",b?1:.7);
      if(b){ const p=P(cam.tx-vw*.45,0,z); if(p) labelAt(c,fmtLat(lat),p[0],p[1]-3); } } }
  const cx=selected?selected.x:cam.tx, cz=selected?selected.z:cam.tz;
  if(L.rings&&!isFar()){ const r0=[5,10,25,50,100,250,500].find(r=>r>=cam.dist*.12)||500; for(const m of [1,2,3,4]){ const r=r0*m; gring(c,cx,cz,r,96,rgba(TH.accent,.4),1);
    const px=cx+r*.7071,pz=cz+r*.7071,p=P(px,groundY(px,pz),pz); if(p) labelAt(c,r+" km",p[0],p[1]-3); } }
}

/* ---------------------------------------------------------------- dynamic layer */
function sweepAndPings(t){
  const cx=selected?selected.x:cam.tx, cz=selected?selected.z:cam.tz, ang=(t*.55)%TAU, R=cam.dist*.55, N=36, da=.05, c0=P(cx,groundY(cx,cz),cz);
  const at=(a)=>{ const x=cx+Math.cos(a)*R,z=cz+Math.sin(a)*R; return P(x,groundY(x,z),z); };
  if(c0) for(let i=0;i<N;i++){ const p1=at(ang-i*da),p2=at(ang-(i+1)*da); if(!p1||!p2) continue;
    ctx.fillStyle=rgba(TH.accent,.2*Math.pow(1-i/N,2)); ctx.beginPath(); ctx.moveTo(c0[0],c0[1]); ctx.lineTo(p1[0],p1[1]); ctx.lineTo(p2[0],p2[1]); ctx.fill(); }
  const e=at(ang); if(c0&&e){ ctx.strokeStyle=rgba(TH.accent,.8); ctx.lineWidth=1.3; ctx.beginPath(); ctx.moveTo(c0[0],c0[1]); ctx.lineTo(e[0],e[1]); ctx.stroke(); }
  return {ang,cx,cz,R};
}
const pingOf=(g,sw)=>{ const dx=g.x-sw.cx,dz=g.z-sw.cz; if(Math.hypot(dx,dz)>sw.R) return 0; return Math.exp(-((((sw.ang-Math.atan2(dz,dx))%TAU)+TAU)%TAU)*2.4); };
function drawGauge(g,t,sw){
  const s=cam.dist/90, col=TH.sc[g.status]||TH.sc.nodata, has=g.stage!=null, sev=SEV[g.status]||0, pg=pingOf(g,sw), y0=groundY(g.x,g.z), base=P(g.x,y0,g.z); if(!base) return;
  const alarm=L.alerts?alerts[g.site_no]:undefined;
  gring(ctx,g.x,g.z,.55*s,28,rgba(col,has?.9:.45),has?1.6:1);
  if(!has){ ctx.fillStyle=rgba(col,.6); ctx.beginPath(); ctx.arc(base[0],base[1],2,0,TAU); ctx.fill(); hits.push({g,x:base[0],y:base[1],r:12}); return; }
  if(sev>=3||alarm){ const ph=((t*.6+g.x*.13)%1); gring(ctx,g.x,g.z,(.65+ph*3)*s,36,rgba(alarm?TH.sc.moderate:col,.6*(1-ph)),1.5); }   // ground pulse
  const h=hOf(g,g.stage), top=P(g.x,y0+h,g.z); if(!top) return;
  const w=Math.max(1.6,.14*s*B.F/base[2]), staff=P(g.x,y0+hOf(g,levelRef(g)),g.z);   // faint gauge staff up to the major level, so the ticks read as a scale
  if(staff){ ctx.strokeStyle=rgba(TH.accent,.28); ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(base[0],base[1]); ctx.lineTo(staff[0],staff[1]); ctx.stroke(); }
  ctx.save(); if(TH.dark) ctx.globalCompositeOperation="lighter";
  ctx.strokeStyle=rgba(col,(TH.dark?.12:.10)+.2*pg); ctx.lineWidth=w*(TH.dark?4:3); ctx.beginPath(); ctx.moveTo(base[0],base[1]); ctx.lineTo(top[0],top[1]); ctx.stroke();
  const gr=ctx.createLinearGradient(base[0],base[1],top[0],top[1]); gr.addColorStop(0,rgba(col,.15)); gr.addColorStop(1,rgba(col,TH.dark?.9:.95));
  ctx.strokeStyle=gr; ctx.lineWidth=w; ctx.stroke(); ctx.restore();
  const tk=.36*s;
  for(const c of CATS){ const lv=g.cats&&g.cats[c]; if(lv==null) continue;                  // official stages as ticks on the pillar
    const hh=y0+hOf(g,lv), a=P(g.x-B.r[0]*tk,hh,g.z-B.r[2]*tk), b=P(g.x+B.r[0]*tk,hh,g.z+B.r[2]*tk);
    if(a&&b){ ctx.strokeStyle=rgba(TH.sc[c],g.stage>=lv?1:.55); ctx.lineWidth=g.stage>=lv?2:1; ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke(); } }
  const or=Math.max(3,.3*s*B.F/top[2]*(1+.25*pg)+(sev>=3?Math.sin(t*4)*.6:0));
  if(TH.dark){ ctx.save(); ctx.globalCompositeOperation="lighter"; const og=ctx.createRadialGradient(top[0],top[1],0,top[0],top[1],or*3); og.addColorStop(0,rgba(col,.85)); og.addColorStop(.3,rgba(col,.35)); og.addColorStop(1,rgba(col,0));
    ctx.fillStyle=og; ctx.beginPath(); ctx.arc(top[0],top[1],or*3,0,TAU); ctx.fill(); ctx.restore(); ctx.fillStyle="#e8f1fb"; ctx.beginPath(); ctx.arc(top[0],top[1],Math.max(1.4,or*.4),0,TAU); ctx.fill(); }
  else { ctx.fillStyle=rgba(col,.2); ctx.beginPath(); ctx.arc(top[0],top[1],or*2.2,0,TAU); ctx.fill(); ctx.fillStyle=col; ctx.strokeStyle="#fff"; ctx.lineWidth=1.5; ctx.beginPath(); ctx.arc(top[0],top[1],Math.max(2.5,or*.8),0,TAU); ctx.fill(); ctx.stroke(); }
  if(alarm){ const sky=P(g.x,y0+h+geo.hmax*.9,g.z); if(sky){ ctx.setLineDash([4,4]); ctx.strokeStyle=rgba(TH.sc.moderate,.9); ctx.lineWidth=1.3; ctx.beginPath(); ctx.moveTo(top[0],top[1]); ctx.lineTo(sky[0],sky[1]); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle=TH.sc.moderate; ctx.font="600 10px Inter,system-ui,sans-serif"; ctx.textAlign="center"; ctx.fillText("ALERT "+String(alarm).toUpperCase(),sky[0],sky[1]-6); } }
  if(g===selected){ ctx.strokeStyle=TH.ink; ctx.lineWidth=1.5; ctx.beginPath(); ctx.arc(top[0],top[1],or*2.6,0,TAU); ctx.stroke(); }
  if(L.labels&&(g===selected||g===hover||sev>=3||cam.dist<160)){ ctx.font="11px Inter,system-ui,sans-serif"; ctx.textAlign="left"; ctx.lineWidth=3; ctx.strokeStyle=TH.dark?"rgba(4,8,18,.85)":"rgba(255,255,255,.9)"; ctx.strokeText(g.lid,top[0]+or*2.3,top[1]+4); ctx.fillStyle=TH.ink; ctx.fillText(g.lid,top[0]+or*2.3,top[1]+4); }
  hits.push({g,x:top[0],y:top[1],r:Math.max(12,or*2.2)});
}
function drawRain(){                                // the circle the mean depth was measured over, never a radar picture
  const N=40;
  for(const g of G){ const mm=g.rain_3h; if(mm==null||mm<=0) continue;
    ctx.fillStyle=rgba(TH.rain,clamp(.14+mm/50*.6,.14,.74)); ctx.beginPath(); let pen=false;
    for(let i=0;i<=N;i++){ const x=g.x+Math.cos(i/N*TAU)*10,z=g.z+Math.sin(i/N*TAU)*10,p=P(x,groundY(x,z),z); if(!p){ pen=false; continue; } pen?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]); pen=true; }
    ctx.fill();
    if(L.labels&&cam.dist<450){ const p=P(g.x,groundY(g.x,g.z),g.z-10); if(p) labelAt(ctx,mm.toFixed(mm<10?1:0)+" mm",p[0],p[1]-3); } }
}
function drawRegions(t){
  const far=isFar();
  for(const r of regions){ if(!far&&!r.planned) continue; const p=P((r.lon-geo.lon0)*geo.k,0,(geo.lat0-r.lat)*110.57); if(!p) continue;
    if(p[0]<-40||p[0]>W+40||p[1]<-40||p[1]>H+40) continue; hits.push(Globe.regionMarker(ctx,p[0],p[1],r,selRegion&&selRegion.id===r.id,t,TH,L)); }
}
function drawHud(){
  const kmPx=cam.dist/B.F, nice=[1,2,5,10,25,50,100,200,500,1000,2000,5000].find(n=>n/kmPx>70)||5000, len=nice/kmPx, rx=W>900?W-310:W-16, bx=rx-len, by=H-30;
  const halo=TH.dark?"rgba(4,8,18,.85)":"rgba(255,255,255,.92)"; ctx.strokeStyle=halo; ctx.lineWidth=4; ctx.beginPath(); ctx.moveTo(bx,by-5); ctx.lineTo(bx,by); ctx.lineTo(bx+len,by); ctx.lineTo(bx+len,by-5); ctx.stroke();
  ctx.strokeStyle=TH.ink; ctx.lineWidth=1.5; ctx.stroke();
  labelAt(ctx,nice+" km",bx+len/2,by-8);
  attribution((bundledBase?"Imagery: NASA Blue Marble (bundled)":imagery?"Imagery: Esri, Maxar, Earthstar Geographics":"Basemap: Esri, HERE, Garmin, © OpenStreetMap contributors")+(terrain&&HG?" · elevation: AWS Terrain Tiles, relief ×"+Math.round(geo.exag):""),rx);
}
function attribution(txt,rx){ ctx.font="10px Inter,system-ui,sans-serif"; ctx.textAlign="right"; ctx.lineWidth=3; ctx.strokeStyle=TH.dark?"rgba(4,8,18,.7)":"rgba(255,255,255,.92)"; ctx.strokeText(txt,rx,H-12); ctx.fillStyle=TH.dark?TH.muted:TH.ink; ctx.fillText(txt,rx,H-12); }
function labelAt(c,txt,x,y){ c.font="10px Inter,system-ui,sans-serif"; c.textAlign="center"; c.lineWidth=3; c.strokeStyle=TH.dark?"rgba(4,8,18,.8)":"rgba(255,255,255,.92)"; c.strokeText(txt,x,y); c.fillStyle=TH.dark?TH.muted:TH.ink; c.fillText(txt,x,y); }
const ease=x=>x*x*(3-2*x);
function globeFrame(now,dt){
  const t=now/1000; if(land!==landTo){ land=landTo>land?Math.min(landTo,land+dt/landDur):Math.max(landTo,land-dt/landDur); }
  Globe.step(dt,interacting); ctx.setTransform(dpr,0,0,dpr,0,0); paintSky(ctx);
  const e=ease(land), Rf=Globe.radius(W,H), Rb=Math.max(W,H)*.56, wide=W>1100, narrow=W<800, cxf=wide?(228+(W-294))/2:W/2, cyf=H/2+18, cxb=narrow?W/2:W*.84, cyb=narrow?H+Rb*.42:H*.56+Rb*.62;
  const o={R:Rf+(Rb-Rf)*e,cx:cxf+(cxb-cxf)*e,cy:cyf+(cyb-cyf)*e,markerA:land>0?Math.max(0,1-land*1.6):1};
  gInfo=Globe.draw(ctx,W,H,dpr,t,TH,regions,selRegion,L,o,G);
  if(land===0){ attribution((Globe.usingBundled()?"Imagery: NASA Blue Marble (bundled)":"Imagery: Esri, Maxar, Earthstar Geographics · Night lights: NASA Black Marble")+" · Terminator approximate",W>900?W-310:W-16); updateHud(now); setViewButtons(); }
  applyFade(dt);
}
function frame(now){
  raf=requestAnimationFrame(frame); const dt=Math.min(.25,(now-lastT)/1000); lastT=now;
  if(!W||!H) return;
  if(view==="globe"){ globeFrame(now,dt); return; }
  if(!geo){ ctx.setTransform(dpr,0,0,dpr,0,0); ctx.fillStyle=TH.sky0; ctx.fillRect(0,0,W,H); return; }
  stepCam(dt); if(cam.auto) cam.yaw+=dt*.05;
  const far=isFar(); if(far!==lastFar){ lastFar=far; drawList(); drawCard(); }
  geo.hmax=cam.dist*.129; basis(); const lod=(interacting||cam.auto||camFly)?1:0;
  const key=[cam.yaw.toFixed(3),cam.pitch.toFixed(3),cam.dist.toFixed(1),cam.tx.toFixed(1),cam.tz.toFixed(1),W,H,terrain,texVer,TH.dark,selected&&selected.site_no,lod,L.grid,L.rings].join("|");
  if(key!==cacheKey){ sctx.setTransform(dpr,0,0,dpr,0,0); drawScene(sctx,lod); cacheKey=key; clearTimeout(patchT); patchT=setTimeout(()=>updatePatch(false),350); }
  ctx.setTransform(1,0,0,1,0,0); ctx.drawImage(scene,0,0); ctx.setTransform(dpr,0,0,dpr,0,0);
  hits=[]; const t=now/1000, sw=L.sweep&&!far?sweepAndPings(t):{ang:0,cx:0,cz:0,R:0};
  if(L.rain&&!far) drawRain();
  if(L.gauges&&!far) for(const o of G.map(g=>({g,z:(P(g.x,0,g.z)||[0,0,1e9])[2]})).sort((a,b)=>b.z-a.z)) drawGauge(o.g,t,sw);
  drawRegions(t); drawHud(); updateHud(now); setViewButtons(); syncNeedle(); applyFade(dt);
}
function resize(){ const r=root.getBoundingClientRect(); dpr=Math.min(2,window.devicePixelRatio||1); W=Math.round(r.width); H=Math.round(r.height);
  for(const c of [cv,scene]){ c.width=W*dpr; c.height=H*dpr; } ctx.setTransform(dpr,0,0,dpr,0,0); cacheKey=""; }

/* ---------------------------------------------------------------- data + panels */
const ageTxt=s=>s==null?"":(s<90?Math.round(s)+" s ago":(s<5400?Math.round(s/60)+" min ago":(s/3600).toFixed(1)+" h ago"));
const STATUS_LABEL={normal:"NORMAL",action:"WATCH",minor:"WARNING",moderate:"SEVERE",major:"CRITICAL",stale:"NORMAL",nodata:"NORMAL"};
const regStatus=r=>r.planned?"PLANNED":(STATUS_LABEL[r.worst]||"NORMAL");
async function pollRegions(){
  try{
    const d=await (await fetch("/api/regions?mode="+mode)).json(); regions=d.regions||[];
    if(regions.length){
      if(!selRegion){ selRegion=regions[0]; if(!initGlobe){ initGlobe=true; if(!landTo) Globe.setView(selRegion.lon,selRegion.lat*.8,1); } }
      else selRegion=regions.find(r=>r.id===selRegion.id)||regions[0];
    }
    ensureGeo(); fillRegionSelect(); if(showRegionList()){ drawList(); drawCard(); }
  }catch(e){}
}
function fillRegionSelect(){ const s=$("m-region"), sig=regions.map(r=>r.id).join(); if(s.dataset.sig!==sig){ s.innerHTML=`<option value="__all">All monitored areas</option>`+regions.map(r=>`<option value="${r.id}">${esc(r.name)}${r.planned?" (planned)":""}</option>`).join(""); s.dataset.sig=sig; if(selRegion) s.value=selRegion.id; } }
async function pollGauges(){
  const live=regions.filter(r=>r.n_gauges>0); if(!live.length) return;
  try{
    const all=await Promise.all(live.map(r=>fetch("/api/gauges?mode="+mode+"&region="+encodeURIComponent(r.id)).then(x=>x.ok?x.json():null)));
    if(all.some(d=>!d)){ note="console server needs a restart: python -m dashboard.flow_monitor"; $("map-note").textContent=note; return; }
    const gs=[]; all.forEach((d,i)=>d.gauges.forEach(g=>{ if(g.lat!=null){ g.region_id=live[i].id; gs.push(g); } }));
    if(geo) gs.forEach(place);
    G=gs; if(selected) selected=G.find(g=>g.site_no===selected.site_no)||null;
    applyRain();
    if(scrub) applyScrub();
    if(!showRegionList()){ drawList(); drawCard(); }
  }catch(e){ note="cannot reach the console server"; $("map-note").textContent=note; }
}
function applyRain(){ let n=0; for(const g of G){ const r=rain[g.site_no]; g.rain_3h=r?r.rain_3h:null; g.rain_end=r?r.hour_end:null; if(g.rain_3h>0) n++; } $("n-rain").textContent=n||""; }
async function pollRain(){
  try{ const d=await (await fetch("/api/rain?mode="+mode)).json(); if(!d.ok) return; rain=d.rain; if(!scrub) applyRain(); }catch(e){}
}
async function pollAlerts(){
  if(scrub) return;
  try{ const d=await (await fetch("/api/advisories")).json(); if(!d.ok) return; const a={}, want=mode==="replay"?"SIMULATION":"LIVE";
    for(const x of d.advisories){ if(x.mode===want&&["draft","approved","sent"].includes(x.status)&&x.alert_status!=="retracted"&&x.alert_status!=="rejected") a[x.site_no]=x.category; }
    alerts=a; }catch(e){}
}
const showRegionList=()=>view==="globe"||isFar();
function drawList(){
  list.innerHTML="";
  if(showRegionList()){
    regions.forEach(r=>{ const d=document.createElement("div"); d.className="grow"+(selRegion&&r.id===selRegion.id?" sel":"");
      d.innerHTML=`<span class="gdot" style="background:${r.planned?"transparent;border:1px dashed "+TH.ink:(TH.sc[r.worst]||TH.sc.nodata)}"></span><div><div class="nm"></div><div class="sub"></div></div>`;
      d.querySelector(".nm").textContent=r.name; d.querySelector(".sub").textContent=r.planned?"planned · no gauges yet":`${r.n_reporting}/${r.n_gauges} gauges · ${regStatus(r)}`;
      d.onclick=()=>{ selectRegion(r); drawList(); drawCard(); }; list.appendChild(d); });
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
  if(showRegionList()){ const r=selRegion;
    if(!r){ card.innerHTML='<div class="sub">GLOBE</div><div class="nm">no monitored areas yet</div>'; return; }
    const col=r.planned?TH.muted:TH.sc[r.worst];
    card.innerHTML=`<div class="sub">${r.planned?"PLANNED AREA":"MONITORED AREA"}</div><div class="nm" style="margin:2px 0 2px"></div><div class="sub" style="margin-bottom:8px"></div>`+
      `<span class="pill">${r.n_gauges} gauges</span><span class="pill">${r.n_reporting} reporting</span><span class="pill" style="color:${col};border-color:${col}">${regStatus(r)}</span>`+
      `<div style="margin-top:10px"><button class="btn go" id="m-enter">Fly to area</button></div><div class="note" style="margin-top:8px">drag to ${view==="globe"?"rotate":"pan"} · wheel to zoom · double-click a marker to fly in</div>`;
    card.querySelector(".nm").textContent=r.name; card.querySelectorAll(".sub")[1].textContent=r.provider||"";
    card.querySelector("#m-enter").onclick=()=>enterRegion(r); return; }
  const g=selected;
  if(!g){ const c={}; G.forEach(x=>c[x.status]=(c[x.status]||0)+1);
    card.innerHTML=`<div class="sub">NETWORK</div><div class="nm" style="margin:2px 0 8px">${G.length} gauges · ${mode==="replay"?"SIMULATION replay":"live"}</div>`+
      Object.keys(SEV).filter(k=>c[k]).map(k=>`<span class="pill" style="color:${TH.sc[k]};border-color:${TH.sc[k]}">${c[k]} ${k}</span>`).join("")+
      `<div class="note" style="margin-top:8px">drag to ${cam.pitch>1.45?"pan":"orbit, shift-drag to pan"} · wheel to zoom · click a gauge<br>pillar height = stage vs that gauge's own official major level; ticks mark the official stages</div>`; return; }
  const cs=g.cats||{}, top=Math.max(cs.major||0,cs.moderate||0,g.stage||0)*1.08||10, pct=v=>clamp(v/top*100,0,100);
  const ticks=CATS.filter(c=>cs[c]!=null).map(c=>`<u style="left:${pct(cs[c])}%;color:${TH.sc[c]}">${c.slice(0,3)} ${cs[c]}</u>`).join("");
  card.innerHTML=`<div class="sub">${g.lid} · ${g.site_no}${g.county?" · "+g.county+" Co.":""}</div><div class="nm">${g.name}</div>`+
    `<div class="val" style="color:${TH.sc[g.status]}">${g.stage==null?"—":g.stage.toFixed(2)}<small> ft</small></div>`+
    `<div class="sub" style="color:${TH.sc[g.status]}">${g.status.toUpperCase()}${g.provisional?" · provisional":""}${g.age_s!=null?" · "+ageTxt(g.age_s):""}</div>`+
    (g.rain_3h!=null?`<div class="sub" style="margin-top:4px">Rain, 3 h to ${utcShort(g.rain_end)}: ${g.rain_3h.toFixed(1)} mm (10 km circle)</div>`:"")+
    `<div class="lvl">${g.stage!=null?`<i style="left:calc(${pct(g.stage)}% - 1px)"></i>`:""}${ticks}</div><div style="height:12px"></div>`+
    (alerts[g.site_no]?`<div class="pill" style="color:${TH.sc.moderate};border-color:${TH.sc.moderate}">open alert: ${alerts[g.site_no]}</div>${scrub?"":'<div style="margin-top:8px"><button class="btn" id="m-review">Review advisory</button></div>'}`:"");
  const rv=card.querySelector("#m-review"); if(rv) rv.onclick=()=>showTab("data");
}
const utcShort=t=>t?new Date(t*1000).toISOString().slice(11,16)+" UTC":"";
function select(g){ selected=g; cacheKey=""; drawList(); drawCard(); }
function selectRegion(r){ selRegion=r; $("m-region").value=r.id; if(view==="globe") Globe.fly(r.lon,r.lat*.8,Globe.st.zoom,1000); }

/* ---------------------------------------------------------------- HUD: status, scene brief, coordinates */
function fmtLat(v){ return Math.abs(v).toFixed(Math.abs(v)%1?2:0)+"°"+(v>=0?"N":"S"); }
function fmtLon(v){ return Math.abs(v).toFixed(Math.abs(v)%1?2:0)+"°"+(v>=0?"E":"W"); }
const esc=t=>String(t??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
function updateHud(now){
  if(now-lastHud<400) return; lastHud=now;
  const iso=new Date(scrub?scrub.t*1000:Date.now()).toISOString(); $("m-clock").textContent=scrub?"Timeline "+iso.slice(0,10)+" "+iso.slice(11,16)+" UTC":iso.slice(0,10)+" "+iso.slice(11,19)+" UTC";
  let worst="normal", brief=""; const nG=G.length, nAl=Object.keys(alerts).length;
  if(scrub){ for(const g of G) if(SEV[g.status]>SEV[worst]) worst=g.status; }
  else for(const r of regions) if(!r.planned&&SEV[r.worst]>SEV[worst]) worst=r.worst;
  if(view==="region"&&!isFar()){
    const rep=G.filter(g=>g.stage!=null).length, hot=G.filter(g=>(SEV[g.status]||0)>=3).sort((a,b)=>SEV[b.status]-SEV[a.status]);
    brief=`<span class="k">Mode</span>${mode==="replay"?"SIMULATION replay":"live"}`+
      `<span class="k">Gauges</span>${rep}/${G.length} reporting`+
      `<span class="k">At or above minor flood</span>`+(hot.length?hot.slice(0,3).map(g=>`${esc(g.name)}: ${g.stage.toFixed(1)} ft (${g.status}; minor ${g.cats&&g.cats.minor!=null?g.cats.minor:"?"} ft)`).join("<br>")+(hot.length>3?`<br>+${hot.length-3} more`:""):"none")+
      `<span class="k">Open alerts</span>${nAl?Object.entries(alerts).map(([s,c])=>esc((G.find(g=>g.site_no===s)||{}).lid||s)+" "+esc(c)).join(", "):"none"}`+
      `<span class="k">Data as of</span>${iso.slice(11,16)} UTC`+(rep===0&&mode==="replay"?"<br>no replay readings seen yet: start a replay":"");
  } else {
    brief=`<span class="k">Mode</span>${mode==="replay"?"SIMULATION replay":"live"}`+regions.map(r=>`<span class="k">${esc(r.name)}</span>`+(r.planned?"planned, no gauges connected yet":`${r.n_reporting}/${r.n_gauges} gauges reporting<br>${r.n_flooding?r.n_flooding+" at or above minor flood":"none at or above minor flood"}`)).join("")+
      `<span class="k">Data as of</span>${iso.slice(11,16)} UTC`;
  }
  const st=$("m-status"); st.textContent=STATUS_LABEL[worst]||"NORMAL"; st.style.color=TH.sc[worst==="stale"||worst==="nodata"?"normal":worst];
  $("n-gauges").textContent=nG||""; $("n-alerts").textContent=String(nAl);
  if($("m-brief").dataset.h!==brief){ $("m-brief").innerHTML=brief; $("m-brief").dataset.h=brief; }
  let co="";
  if(view==="globe"){ const sd=Globe.sunDeg(); const ll=gInfo&&Globe.fromScreen(ptr.x,ptr.y,gInfo.cx,gInfo.cy,gInfo.R);
    co=`${ll?"CURSOR "+fmtLat(+(ll[1]/D2R).toFixed(2))+" "+fmtLon(+wrapDeg(ll[0]/D2R).toFixed(2)):"CURSOR —"}<br>ZOOM ×${Globe.st.zoom.toFixed(1)} · SUB-SOLAR ${fmtLat(+sd.lat.toFixed(1))} ${fmtLon(+wrapDeg(sd.lon).toFixed(1))}`; }
  else if(geo) co=`CENTRE ${fmtLat(+toLat(cam.tz).toFixed(2))} ${fmtLon(+toLon(cam.tx).toFixed(2))}<br>ALT ${Math.round(cam.dist*Math.sin(cam.pitch))} KM · PITCH ${Math.round(cam.pitch/D2R)}°`;
  $("m-coords").innerHTML=co;
}
const wrapDeg=d=>((d+540)%360+360)%360-180;

/* ---------------------------------------------------------------- globe <-> flat map, at the same place and scale */
const baseR=()=>Math.min(W,H)*.38, zoomForDist=d=>clamp((F()*6371/d)/baseR(),.6,160);
function snapshot(){ if(!fadeCv) fadeCv=document.createElement("canvas"); fadeCv.width=cv.width; fadeCv.height=cv.height; fadeCv.getContext("2d").drawImage(cv,0,0); fadeA=1; }
function applyFade(dt){ if(fadeA<=0||!fadeCv) return; ctx.setTransform(1,0,0,1,0,0); ctx.globalAlpha=fadeA; ctx.drawImage(fadeCv,0,0); ctx.globalAlpha=1; ctx.setTransform(dpr,0,0,dpr,0,0); fadeA=Math.max(0,fadeA-dt*1.8); }
function regionFit(r){
  const gs=G.filter(g=>g.region_id===r.id);
  if(!gs.length) return {tx:(r.lon-geo.lon0)*geo.k,tz:(geo.lat0-r.lat)*110.57,dist:1300};
  const xs=gs.map(g=>g.x), zs=gs.map(g=>g.z), x0=Math.min(...xs),x1=Math.max(...xs),z0=Math.min(...zs),z1=Math.max(...zs);
  return {tx:(x0+x1)/2,tz:(z0+z1)/2,dist:Math.max(x1-x0,z1-z0,40)*1.75};
}
function toFlat(lon,lat,dist){
  if(!geo) return; snapshot(); cam.tx=(lon-geo.lon0)*geo.k; cam.tz=(geo.lat0-lat)*110.57; clampTarget(); cam.dist=clamp(dist,DIST_MIN,DIST_MAX); cam.yaw=0; cam.auto=false;
  cam.pitch=$("m-tilt").checked?TILT:FLAT; view="region"; flying=false; cacheKey=""; lastFar=null; syncAuto(); updatePatch(true);
}
function enterRegion(r){
  if(!r||flying||!geo) return; selRegion=r; $("m-region").value=r.id; const fit=regionFit(r);
  if(view==="globe"){ flying=true; Globe.fly(r.lon,r.lat,zoomForDist(fit.dist),1500,()=>toFlat(r.lon,r.lat,fit.dist)); }
  else { selected=null; flyCam(fit.tx,fit.tz,fit.dist,1200); drawList(); drawCard(); }
}
function fitAll(){
  if(!regions.length||!geo) return;
  if(view==="globe"){ Globe.fly(regions[0].lon,regions[0].lat*.8,1,1000); return; }
  const xs=regions.map(r=>(r.lon-geo.lon0)*geo.k), zs=regions.map(r=>(geo.lat0-r.lat)*110.57), x0=Math.min(...xs),x1=Math.max(...xs),z0=Math.min(...zs),z1=Math.max(...zs);
  selected=null; flyCam((x0+x1)/2,(z0+z1)/2,Math.max((x1-x0)*F()/W,(z1-z0)*F()/H,3000)*1.5,1400);
}
function toGlobe(){
  if(view==="globe"||flying||!geo) return; const lon=toLon(cam.tx), lat=clamp(toLat(cam.tz),-75,75); snapshot(); clearTimeout(patchT); view="globe"; Globe.setView(lon,lat,zoomForDist(cam.dist)); flying=true;
  Globe.fly(lon,lat*.8,1,1500,()=>{ flying=false; }); syncAuto(); drawList(); drawCard();
}
function globeToFlat(){ if(view!=="globe"||flying||!geo) return; const R=Globe.radius(W,H); toFlat(wrapDeg(Globe.st.lon/D2R),Globe.st.lat/D2R,F()*6371/R); }

/* ---------------------------------------------------------------- input */
let drag=null, ptr={x:0,y:0}, wheelT=0;
function hitAt(mx,my,arr){ let best=null,bd=1e9; for(const h of arr){ const d=Math.hypot(h.x-mx,h.y-my); if(d<h.r&&d<bd){ best=h; bd=d; } } return best; }
const regionAt=(mx,my)=>{ const h=hitAt(mx,my,view==="globe"?Globe.hits():hits.filter(x=>x.region)); return h&&h.region; };
cv.addEventListener("contextmenu",e=>e.preventDefault());
cv.addEventListener("pointerdown",e=>{ cv.setPointerCapture(e.pointerId); drag={x:e.clientX,y:e.clientY,moved:0,pan:e.shiftKey||e.button===2}; cv.classList.add("drag"); interacting=true; camFly=null; });
cv.addEventListener("pointermove",e=>{
  const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top; ptr={x:mx,y:my};
  if(drag){ const dx=e.clientX-drag.x,dy=e.clientY-drag.y; drag.moved+=Math.abs(dx)+Math.abs(dy); drag.x=e.clientX; drag.y=e.clientY; tip.hidden=true;
    if(view==="globe"){ Globe.drag(dx,dy,W,H); syncAuto(); return; }
    if(drag.pan||cam.pitch>1.45){ const k=cam.dist/B.F, kf=k/Math.max(.2,Math.sin(cam.pitch)); cam.tx-=dx*Math.cos(cam.yaw)*k+dy*Math.sin(cam.yaw)*kf; cam.tz+=dx*Math.sin(cam.yaw)*k-dy*Math.cos(cam.yaw)*kf; clampTarget(); }
    else{ cam.yaw-=dx*.006; cam.pitch=clamp(cam.pitch+dy*.005,.12,FLAT); cam.auto=false; syncAuto(); } return; }
  const h=view==="globe"?hitAt(mx,my,Globe.hits()):hitAt(mx,my,hits); hover=h&&h.g?h.g:null; cv.style.cursor=h?"pointer":"grab";
  if(!h){ tip.hidden=true; return; }
  tip.hidden=false; tip.style.left=mx+"px"; tip.style.top=my+"px";
  tip.textContent=h.region?(h.region.planned?`${h.region.name} · planned, no gauges yet`:`${h.region.name} · ${h.region.n_reporting}/${h.region.n_gauges} gauges · ${regStatus(h.region)}`):`${h.g.name} · ${h.g.stage==null?"no reading":h.g.stage.toFixed(2)+" ft"} · ${h.g.status}`;
});
cv.addEventListener("pointerup",e=>{ cv.classList.remove("drag"); interacting=false;
  if(drag&&drag.moved<5){ const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
    const rg=regionAt(mx,my); if(rg){ selRegion=rg; $("m-region").value=rg.id; drawList(); drawCard(); }
    else if(view==="region"){ const h=hitAt(mx,my,hits); select(h&&h.g?h.g:null); } }
  drag=null; });
cv.addEventListener("pointerleave",()=>{ hover=null; tip.hidden=true; });
cv.addEventListener("wheel",e=>{ e.preventDefault(); interacting=true; clearTimeout(wheelT); wheelT=setTimeout(()=>interacting=false,250); camFly=null;
  if(view==="globe"){ Globe.zoom(e.deltaY); Globe.st.fly=null; return; } if(!geo) return; cam.dist=clamp(cam.dist*Math.exp(e.deltaY*.0012),DIST_MIN,DIST_MAX); },{passive:false});
cv.addEventListener("dblclick",()=>{ const rg=regionAt(ptr.x,ptr.y); if(view==="globe"){ const t=rg||selRegion; if(t) enterRegion(t); } else if(rg) enterRegion(rg); else if(selRegion) enterRegion(selRegion); });
const autoOn=()=>view==="globe"?Globe.st.auto:cam.auto;
function syncAuto(){ $("m-auto").setAttribute("aria-pressed",autoOn()); }
let lastBtn="";
function setViewButtons(){ const flat=cam.pitch>1.45, k=view+flat; if(k===lastBtn) return; lastBtn=k; const g=view==="globe";
  $("v-globe").setAttribute("aria-pressed",g); $("v-flat").setAttribute("aria-pressed",!g); $("m-terrain").disabled=g; $("m-tilt").disabled=g; $("m-imagery").disabled=g; $("m-tilt").setAttribute("aria-pressed",!g&&!flat); drawCard(); }
function syncNeedle(){ const y=view==="globe"?0:cam.yaw; if(y!==syncNeedle.y){ syncNeedle.y=y; $("m-needle").style.transform=`rotate(${y}rad)`; } }
function setSeg(live){ $("m-live").setAttribute("aria-pressed",live); $("m-replay").setAttribute("aria-pressed",!live); }
$("m-auto").onclick=()=>{ if(view==="globe") Globe.st.auto=!Globe.st.auto; else cam.auto=!cam.auto; syncAuto(); };
$("v-globe").onclick=toGlobe;
$("v-flat").onclick=globeToFlat;
$("m-tilt").onclick=()=>{ const tilted=cam.pitch<=1.45; cam.pitch=tilted?FLAT:TILT; if(tilted){ cam.yaw=0; cam.auto=false; syncAuto(); } cacheKey=""; };
const switchMode=m=>{ closeScrub(); mode=m; setSeg(m==="live"); G=[]; alerts={}; rain={}; selected=null; return pollRegions().then(()=>Promise.all([pollRain(),pollAlerts()])).then(pollGauges); };
$("m-live").onclick=()=>switchMode("live");
$("m-replay").onclick=()=>switchMode("replay");
$("m-terrain").onclick=()=>{ terrain=!terrain; $("m-terrain").setAttribute("aria-pressed",terrain); cacheKey=""; note=""; $("map-note").textContent=""; if(terrain){ if(baseTex) loadDem(); else updatePatch(true); noteTerrain(); if(cam.pitch>1.45){ cam.pitch=TILT; } } else updatePatch(true); };
$("m-imagery").onclick=()=>{ imagery=!imagery; $("m-imagery").setAttribute("aria-pressed",imagery); if(geo){ loadWorld(); updatePatch(true); } };
$("m-region").onchange=e=>{ if(e.target.value==="__all"){ fitAll(); return; } const r=regions.find(x=>x.id===e.target.value); if(!r) return; selRegion=r; if(view==="globe"){ Globe.fly(r.lon,r.lat*.8,Globe.st.zoom,1100); drawList(); drawCard(); } else enterRegion(r); };
document.querySelectorAll("[data-layer]").forEach(cb=>{ cb.onchange=()=>{ L[cb.dataset.layer]=cb.checked; cacheKey=""; }; });

/* ---------------------------------------------------------------- timeline: scrub the windows stored in PostgreSQL across the map */
const catOf=(s,c)=>{ let o="normal"; for(const k of CATS) if(c&&c[k]!=null&&s>=c[k]) o=k; return o; };
const utc=t=>new Date(t*1000).toISOString().slice(0,16).replace("T"," ");
function applyScrub(){
  const s=scrub; if(!s) return;
  for(const g of G){ const a=s.series[g.site_no]; let r=null; if(a) for(const x of a){ if(x[0]>s.t) break; r=x; }
    g.stage=r?r[1]:null; g.status=r?catOf(r[1],g.cats):"nodata"; g.age_s=null; g.provisional=false; g.rain_3h=r&&r[3]!=null?r[3]:null; g.rain_end=g.rain_3h!=null?r[0]+3600:null; }
  for(const r of regions){ if(r.planned) continue; const gs=G.filter(g=>g.region_id===r.id); r.n_reporting=gs.filter(g=>g.stage!=null).length; r.n_flooding=gs.filter(g=>SEV[g.status]>=3).length;
    r.worst=gs.reduce((w,g)=>SEV[g.status]>SEV[w]?g.status:w,"normal"); }
  const on={}; for(const a of s.alerts) if(a.t<=s.t&&a.status!=="retracted") on[a.site_no]=a.category; alerts=on;
  $("sc-t").textContent=`${utc(s.t)} UTC · ${s.mode==="SIMULATION"?"SIMULATION":"live history"}`+(s.alerts.some(a=>a.t>s.t-3600&&a.t<=s.t)?" · alert raised here":"");
  $("sc-range").value=s.t; $("n-rain").textContent=G.filter(g=>g.rain_3h>0).length||""; lastHud=0; drawList(); drawCard();
}
function stopPlay(){ if(scrub&&scrub.timer){ clearInterval(scrub.timer); scrub.timer=0; } $("sc-play").textContent="▶"; $("sc-play").setAttribute("aria-label","Play"); }
async function loadScrub(start,end){
  const q=start!=null?`&start=${start}&end=${end}`:"", d=await (await fetch(`/api/timeline?mode=${mode}${q}`)).json();
  if(!d.ok||!d.events.length||d.start==null){ $("map-note").textContent=d.ok?"no stored windows to scrub yet for this mode":d.error; closeScrub(); return; }
  stopPlay(); scrub={...d,t:d.start,timer:0}; const rg=$("sc-range"); rg.min=d.start; rg.max=d.end-3600; rg.step=3600;
  $("sc-ev").innerHTML=d.events.map(e=>`<option value="${e.start}|${e.end}"${e.start===d.start?" selected":""}>${utc(e.start).slice(0,10)} to ${utc(e.end-3600).slice(0,10)}${e.alerts?` · ${e.alerts} alert${e.alerts>1?"s":""}`:""}</option>`).join("");
  $("sc-ticks").innerHTML=d.alerts.map(a=>`<i class="${a.status==="retracted"?"off":""}" style="left:${((a.t-d.start)/Math.max(1,d.end-3600-d.start)*100).toFixed(1)}%;background:var(--s-${esc(a.category)})" title="${esc(a.category)} alert, ${utc(a.t)} UTC${a.status==="retracted"?", retracted":""}"></i>`).join("");
  applyScrub();
}
function openScrub(){ $("m-scrub").hidden=false; $("m-time").setAttribute("aria-pressed",true); loadScrub().then(()=>{ if(scrub&&selRegion&&selRegion.n_gauges) enterRegion(selRegion); }); }
function closeScrub(){ if(!scrub&&$("m-scrub").hidden) return; stopPlay(); scrub=null; $("m-scrub").hidden=true; $("m-time").setAttribute("aria-pressed",false); selected=null; pollGauges(); pollAlerts(); pollRain(); }
$("m-time").onclick=()=>{ scrub||!$("m-scrub").hidden?closeScrub():openScrub(); };
$("sc-close").onclick=closeScrub;
$("sc-range").oninput=e=>{ if(!scrub) return; stopPlay(); scrub.t=+e.target.value; applyScrub(); };
$("sc-ev").onchange=e=>{ const [a,b]=e.target.value.split("|").map(Number); loadScrub(a,b); };
$("sc-play").onclick=()=>{ const s=scrub; if(!s) return; if(s.timer){ stopPlay(); return; } if(s.t>=s.end-3600) s.t=s.start; $("sc-play").textContent="❚❚"; $("sc-play").setAttribute("aria-label","Pause");
  s.timer=setInterval(()=>{ if(s.t>=s.end-3600){ stopPlay(); return; } s.t+=3600; applyScrub(); },220); };
document.addEventListener("themechange",()=>{ readTheme(); cacheKey=""; if(geo){ if(!imagery){ loadWorld(); updatePatch(true); } if(dem) composeTerrain(); } drawList(); drawCard(); });

window.Map3D={
  show(){ if(running) return; running=true; resize(); lastT=performance.now(); raf=requestAnimationFrame(frame);
    pollRegions().then(()=>{ pollRain().then(pollGauges); pollAlerts(); });
    pollA=setInterval(()=>{ pollRegions(); pollGauges(); },4000); pollB=setInterval(()=>{ pollAlerts(); pollRain(); },6000); },
  hide(){ running=false; cancelAnimationFrame(raf); clearInterval(pollA); clearInterval(pollB); },
  toLanding(instant){                                         // same canvas: the Earth grows and sinks behind the landing page, facing the terminator whatever the hour
    closeScrub(); selected=null; hover=null; tip.hidden=true; camFly=null; clearTimeout(patchT); flying=false; landTo=1;
    const target=wrapDeg(Globe.sunDeg().lon-35), fromMap=view!=="globe";
    if(fromMap&&geo&&!instant){ snapshot(); Globe.setView(toLon(cam.tx),clamp(toLat(cam.tz),-60,60),zoomForDist(cam.dist)); }
    view="globe";
    if(instant){ Globe.setView(target,18,1); Globe.st.auto=true; land=1; return; }
    landDur=1.3; Globe.fly(target,18,1,1300,()=>{ Globe.st.auto=true; }); },
  focusAlert(site,sim){                                       // alert line -> fly to that gauge and open its card
    const want=sim?"replay":"live";
    (mode===want?pollGauges():switchMode(want)).then(()=>{
      const g=G.find(x=>x.site_no===site); if(!g||!geo) return; const r=regions.find(x=>x.id===g.region_id); if(r){ selRegion=r; $("m-region").value=r.id; }
      if(view==="globe"){ flying=true; Globe.fly(g.lon,g.lat,zoomForDist(90),1500,()=>{ toFlat(g.lon,g.lat,90); select(g); }); }
      else { select(g); flyCam(g.x,g.z,90,1100); } }); },
  fromLanding(ms){ landDur=(ms||1900)/1000; landTo=0; if(selRegion){ Globe.fly(selRegion.lon,selRegion.lat*.8,1,ms||1900,()=>{ syncAuto(); }); } drawList(); drawCard(); },
  summary:()=>({regions,alerts})
};
if(!$("tab-map").hidden&&!document.body.classList.contains("landing")) window.Map3D.show();
if(window.ResizeObserver) new ResizeObserver(()=>{ if(running) resize(); }).observe(root); else addEventListener("resize",()=>{ if(running) resize(); });
})();
