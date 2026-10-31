/* Globe: an orthographic sphere drawn by a WebGL2 fragment shader at full screen resolution. Imagery = Esri World Imagery in
   Web Mercator, loaded in steps (zoom 2, 3, 4 = up to 4096 px around the world) plus a sharper patch for the area in view once
   zoomed in. Night side = NASA Black Marble city lights. Day/night terminator is approximate (sub-solar point from the UTC date,
   no equation of time). Markers: one per monitored region, gauge dots when zoomed in. Add a region in config/regions.json and it
   appears here. Used by map3d.js; no library. */
(function(){
"use strict";
const TAU=Math.PI*2, D2R=Math.PI/180;
const st={lon:-98.3*D2R,lat:22*D2R,zoom:1,fly:null,auto:true};
let hits=[], gl=null, glc=null, prog=null, U={}, ready=false, failed=false, maxTex=4096, imgZ=-1, loadingBase=false, lightsOk=false, patch=null, patchKey="", patchVer=0, patchT=0;

const VS=`#version 300 es
in vec2 a; void main(){ gl_Position=vec4(a,0.,1.); }`;
const FS=`#version 300 es
precision highp float;
uniform vec2 uC; uniform float uR,uLon,uLat,uPx;
uniform vec3 uSun,uAtm;
uniform sampler2D uTex,uNight,uPatch;
uniform vec4 uD; uniform float uDOn,uNightAmt,uLights,uSat,uMul,uHaveTex;
out vec4 o;
const float PI=3.14159265358979, TAU=6.28318530717959;
void main(){
  vec2 p=(gl_FragCoord.xy-uC)/uR; float r=length(p), a=1.-smoothstep(1.-uPx,1.+uPx,r);
  float sunSide=clamp(dot(normalize(vec3(p,.35)),uSun)*.75+.55,.18,1.);
  float out0=clamp(1.-(r-1.)/.2,0.,1.), glow=(r>1.?pow(out0,3.2)*.75:0.)*sunSide;
  if(r>=1.+uPx*2.){ o=vec4(uAtm*glow,glow); return; }
  float z=sqrt(max(0.,1.-min(1.,r*r))), s0=sin(uLat), c0=cos(uLat);
  float lat=asin(clamp(z*s0+p.y*c0,-1.,1.)), lon=uLon+atan(p.x,z*c0-p.y*s0);
  float u=lon/TAU+.5; u-=floor(u);
  float ua=u, ub=fract(u+.5)-.5; u=fwidth(ua)<fwidth(ub)+1e-6?ua:ub;      // no seam at the antimeridian
  float v=.5-log(tan(PI/4.+clamp(lat,-1.4835,1.4835)/2.))/TAU;
  vec3 c=uHaveTex>.5?texture(uTex,vec2(u,v)).rgb:uAtm*.45;
  if(uDOn>.5){ vec2 d=(vec2(fract(u),v)-uD.xy)/uD.zw; if(d.x>0.&&d.x<1.&&d.y>0.&&d.y<1.){ vec2 e=min(d,1.-d); float f=smoothstep(0.,.03,min(e.x,e.y)); c=mix(c,texture(uPatch,d).rgb,f); } }
    float l=dot(c,vec3(.3,.59,.11)); c=mix(vec3(l),c,uSat)*uMul;
  float dt=dot(vec3(p,z),uSun), day=smoothstep(-.08,.22,dt);
  c=mix(c,vec3(.6,.64,.7),smoothstep(1.43,1.48,abs(lat))*.7*day);
  vec3 col=c*(uNightAmt+(1.-uNightAmt)*day)*(.62+.38*pow(z,.45));
  col*=mix(vec3(.82,.88,1.15),vec3(1.),day);
  col+=texture(uNight,vec2(u,v)).rgb*vec3(1.,.82,.58)*(1.-day)*uLights*1.15;
  col=mix(col,uAtm,pow(1.-z,3.)*(.25+.5*day));                            // atmosphere at the limb
  col=clamp(col,0.,1.);
  vec3 pm=col*a+uAtm*glow*(1.-a); o=vec4(pm,a+glow*(1.-a));
}`;

/* ---------------------------------------------------------------- GL set-up */
function initGL(){
  glc=document.createElement("canvas"); gl=glc.getContext("webgl2",{alpha:true,premultipliedAlpha:true,antialias:false});
  if(!gl){ failed=true; return; }
  gl.getExtension("OES_standard_derivatives"); maxTex=gl.getParameter(gl.MAX_TEXTURE_SIZE);
  const sh=(t,s)=>{ const x=gl.createShader(t); gl.shaderSource(x,s); gl.compileShader(x); if(!gl.getShaderParameter(x,gl.COMPILE_STATUS)) console.error(gl.getShaderInfoLog(x)); return x; };
  prog=gl.createProgram(); gl.attachShader(prog,sh(gl.VERTEX_SHADER,VS)); gl.attachShader(prog,sh(gl.FRAGMENT_SHADER,FS)); gl.linkProgram(prog);
  if(!gl.getProgramParameter(prog,gl.LINK_STATUS)){ console.error(gl.getProgramInfoLog(prog)); failed=true; return; }
  gl.useProgram(prog);
  gl.bindBuffer(gl.ARRAY_BUFFER,gl.createBuffer()); gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
  const a=gl.getAttribLocation(prog,"a"); gl.enableVertexAttribArray(a); gl.vertexAttribPointer(a,2,gl.FLOAT,false,0,0);
  for(const n of ["uC","uR","uLon","uLat","uPx","uSun","uAtm","uTex","uNight","uPatch","uD","uDOn","uNightAmt","uLights","uSat","uMul","uHaveTex"]) U[n]=gl.getUniformLocation(prog,n);
  gl.uniform1i(U.uTex,0); gl.uniform1i(U.uNight,1); gl.uniform1i(U.uPatch,2);
  for(const unit of [0,1,2]){ gl.activeTexture(gl.TEXTURE0+unit); gl.bindTexture(gl.TEXTURE_2D,gl.createTexture()); gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([0,0,0,255])); }
  ready=true; loadBase(); loadLights();
}
function upload(unit,src,wrapX){
  gl.activeTexture(gl.TEXTURE0+unit);
  gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,src); gl.generateMipmap(gl.TEXTURE_2D);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,wrapX?gl.REPEAT:gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  const an=gl.getExtension("EXT_texture_filter_anisotropic"); if(an) gl.texParameterf(gl.TEXTURE_2D,an.TEXTURE_MAX_ANISOTROPY_EXT,Math.min(8,gl.getParameter(an.MAX_TEXTURE_MAX_ANISOTROPY_EXT)));
}

/* ---------------------------------------------------------------- imagery */
const imageryUrl=(z,x,y)=>`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
const lightsUrl=(z,x,y)=>`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/default/2016-01-01/GoogleMapsCompatible_Level8/${z}/${y}/${x}.png`;
function loadImg(u){ return new Promise(res=>{ const im=new Image(); im.crossOrigin="anonymous"; im.onload=()=>res(im); im.onerror=()=>res(null); im.src=u; }); }
async function mosaic(z,x0,y0,nx,ny,url){
  const n=1<<z, c=document.createElement("canvas"); c.width=nx*256; c.height=ny*256; const g=c.getContext("2d"); let ok=0, jobs=[];
  for(let j=0;j<ny;j++) for(let i=0;i<nx;i++){ const y=y0+j; if(y<0||y>=n) continue; const x=((x0+i)%n+n)%n;
    jobs.push(loadImg(url(z,x,y)).then(im=>{ if(im){ g.drawImage(im,i*256,j*256); ok++; } })); }
  await Promise.all(jobs); return ok?c:null;
}
async function loadBase(){                                     // coarse first so the globe appears at once, then sharper
  if(loadingBase) return; loadingBase=true;
  for(const z of [2,3,4]){ if((256<<z)>maxTex) break; const n=1<<z, m=await mosaic(z,0,0,n,n,imageryUrl); if(m){ upload(0,m,true); imgZ=z; } }
  loadingBase=false;
}
async function loadLights(){ const m=await mosaic(3,0,0,8,8,lightsUrl); if(m){ upload(1,m,true); lightsOk=true; } }
function wantPatch(R,dpr,W,H){                                 // sharper tiles for the area in view, once the base texture is too coarse
  const z=Math.min(14,Math.ceil(Math.log2(TAU*R*dpr/256))); if(imgZ<0||z<=imgZ){ if(patch){ patch=null; patchKey=""; } return; }
  const n=1<<z, u=st.lon/TAU+.5, mx=(u-Math.floor(u))*n, my=(.5-Math.log(Math.tan(Math.PI/4+st.lat/2))/TAU)*n;
  const nx=Math.min(10,Math.ceil(W*dpr/256)+2), ny=Math.min(8,Math.ceil(H*dpr/256)+2), x0=Math.floor(mx-nx/2), y0=Math.floor(my-ny/2), key=[z,x0,y0,nx,ny].join();
  if(key===patchKey) return; patchKey=key; const ver=++patchVer;
  mosaic(z,x0,y0,nx,ny,imageryUrl).then(m=>{ if(ver!==patchVer||!m) return; upload(2,m,false); patch={x:x0/n,y:y0/n,w:nx/n,h:ny/n}; });
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
const radius=(W,H)=>Math.min(W,H)*.38*st.zoom;

/* ---------------------------------------------------------------- draw */
function sphere(W,H,dpr,cx,cy,R,dark){
  const pw=Math.round(W*dpr), ph=Math.round(H*dpr); if(glc.width!==pw||glc.height!==ph){ glc.width=pw; glc.height=ph; gl.viewport(0,0,pw,ph); }
  const sp=subsolar(new Date()), sv=toView(sp.lon,sp.lat);
  gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.uniform2f(U.uC,cx*dpr,ph-cy*dpr); gl.uniform1f(U.uR,R*dpr); gl.uniform1f(U.uPx,1.2/(R*dpr)); gl.uniform1f(U.uLon,st.lon); gl.uniform1f(U.uLat,st.lat);
  gl.uniform3f(U.uSun,sv[0],sv[1],sv[2]);
  if(dark){ gl.uniform3f(U.uAtm,.36,.62,1); gl.uniform1f(U.uNightAmt,.24); gl.uniform1f(U.uLights,lightsOk?1:0); gl.uniform1f(U.uSat,.85); gl.uniform1f(U.uMul,.95); }
  else { gl.uniform3f(U.uAtm,.55,.82,1); gl.uniform1f(U.uNightAmt,.6); gl.uniform1f(U.uLights,lightsOk?.25:0); gl.uniform1f(U.uSat,1); gl.uniform1f(U.uMul,1.08); }
  gl.uniform1f(U.uHaveTex,imgZ>=0?1:0);
  gl.uniform1f(U.uDOn,patch?1:0); if(patch) gl.uniform4f(U.uD,patch.x,patch.y,patch.w,patch.h);
  gl.drawArrays(gl.TRIANGLES,0,3);
  return glc;
}
function draw(ctx,W,H,dpr,t,T,regions,sel,L,o,gauges){
  if(!gl&&!failed) initGL();
  const R=o.R, cx=o.cx, cy=o.cy, mA=o.markerA==null?1:o.markerA;
  if(ready){
    ctx.drawImage(sphere(W,H,dpr,cx,cy,R,T.dark),0,0,W,H);
    if(imgZ<0&&mA>.5){ ctx.fillStyle=T.muted; ctx.font="12px Inter,system-ui,sans-serif"; ctx.textAlign="center"; ctx.fillText("loading globe imagery…",cx,cy); }
  } else { ctx.fillStyle=T.dark?"#0b1730":"#a9d8f5"; ctx.beginPath(); ctx.arc(cx,cy,R,0,TAU); ctx.fill(); if(mA>.5){ ctx.fillStyle=T.muted; ctx.font="12px Inter,system-ui,sans-serif"; ctx.textAlign="center"; ctx.fillText("WebGL2 unavailable: globe shown without imagery. Use the flat map.",cx,cy); } }
  clearTimeout(patchT); if(ready&&!st.fly&&mA>=1) patchT=setTimeout(()=>wantPatch(R,dpr,W,H),300);
  hits=[]; if(mA<=0) return {cx,cy,R};
  ctx.save(); ctx.globalAlpha=mA;
  if(L.grid){ const step=st.zoom>12?5:st.zoom>4?10:30, ds=Math.max(.5,step/10); ctx.lineWidth=.6; ctx.strokeStyle=T.dark?"rgba(170,205,245,.16)":"rgba(255,255,255,.34)";
    for(let lon=-180;lon<180;lon+=step){ ctx.beginPath(); let pen=false; for(let lat=-85;lat<=85;lat+=ds){ const v=toView(lon*D2R,lat*D2R); if(v[2]<=0){ pen=false; continue; } const x=cx+v[0]*R,y=cy-v[1]*R; pen?ctx.lineTo(x,y):ctx.moveTo(x,y); pen=true; } ctx.stroke(); }
    for(let lat=-80+((80%step)?(step-80%step)%step:0);lat<=80;lat+=step){ ctx.beginPath(); let pen=false; for(let lon=-180;lon<=180;lon+=ds){ const v=toView(lon*D2R,lat*D2R); if(v[2]<=0){ pen=false; continue; } const x=cx+v[0]*R,y=cy-v[1]*R; pen?ctx.lineTo(x,y):ctx.moveTo(x,y); pen=true; } ctx.stroke(); } }
  if(L.gauges!==false&&gauges&&st.zoom>2.2) for(const g of gauges){ const v=toView(g.lon*D2R,g.lat*D2R); if(v[2]<=.02) continue; const x=cx+v[0]*R,y=cy-v[1]*R; ctx.fillStyle=T.sc[g.status]||T.sc.nodata; ctx.beginPath(); ctx.arc(x,y,st.zoom>6?3.5:2.2,0,TAU); ctx.fill(); ctx.strokeStyle=T.dark?"rgba(4,8,18,.8)":"rgba(255,255,255,.9)"; ctx.lineWidth=1; ctx.stroke(); }
  if(L.regions!==false) for(const r of regions){
    const v=toView(r.lon*D2R,r.lat*D2R); if(v[2]<=.02) continue;
    const m=regionMarker(ctx,cx+v[0]*R,cy-v[1]*R,r,sel&&sel.id===r.id,t,T,L); hits.push(m);
  }
  ctx.restore();
  return {cx,cy,R};
}
function regionMarker(ctx,x,y,r,isSel,t,T,L){                  // shared with the flat map
  const col=r.planned?T.muted:(T.sc[r.worst]||T.sc.nodata), hot=r.n_flooding>0;
  if(!r.planned){ const ph=(t*.5)%1, a=ctx.globalAlpha; ctx.strokeStyle=col; ctx.globalAlpha=a*.55*(1-ph); ctx.lineWidth=1.4; ctx.beginPath(); ctx.arc(x,y,6+ph*20,0,TAU); ctx.stroke(); ctx.globalAlpha=a;
    ctx.fillStyle=col; ctx.beginPath(); ctx.arc(x,y,hot?5:4,0,TAU); ctx.fill(); ctx.strokeStyle=T.dark?"#050a14":"#fff"; ctx.lineWidth=1.5; ctx.stroke(); }
  else { ctx.setLineDash([3,3]); ctx.strokeStyle=T.ink; ctx.lineWidth=1.2; ctx.beginPath(); ctx.arc(x,y,5,0,TAU); ctx.stroke(); ctx.setLineDash([]); }
  ctx.strokeStyle=T.ink; ctx.lineWidth=1; const q=isSel?13:10; for(const [dx,dy] of [[-1,-1],[1,-1],[1,1],[-1,1]]){ ctx.beginPath(); ctx.moveTo(x+dx*q,y+dy*(q-5)); ctx.lineTo(x+dx*q,y+dy*q); ctx.lineTo(x+dx*(q-5),y+dy*q); ctx.stroke(); }   // target brackets
  if(L.labels!==false){ ctx.font="600 11px Inter,system-ui,sans-serif"; ctx.textAlign="left"; ctx.lineWidth=3; ctx.strokeStyle=T.dark?"rgba(4,8,18,.85)":"rgba(255,255,255,.9)"; ctx.strokeText(r.name.toUpperCase(),x+18,y+4); ctx.fillStyle=T.ink; ctx.fillText(r.name.toUpperCase(),x+18,y+4);
    const sub=r.planned?"planned · no gauges yet":`${r.n_reporting}/${r.n_gauges} gauges${r.n_flooding?" · "+r.n_flooding+" in flood":""}`;
    ctx.font="10px Inter,system-ui,sans-serif"; ctx.strokeText(sub,x+18,y+17); ctx.fillStyle=T.dark?T.muted:T.ink; ctx.fillText(sub,x+18,y+17); }
  return {region:r,x,y,r:20};
}

/* ---------------------------------------------------------------- state, input, flight */
function step(dt,interacting){
  if(st.fly){ const f=st.fly; f.t=Math.min(1,f.t+dt/f.dur); const e=f.t<.5?2*f.t*f.t:1-Math.pow(-2*f.t+2,2)/2;
    st.lon=f.lon0+(f.lon1-f.lon0)*e; st.lat=f.lat0+(f.lat1-f.lat0)*e; st.zoom=Math.exp(Math.log(f.z0)+(Math.log(f.z1)-Math.log(f.z0))*e);
    if(f.t>=1){ const d=f.done; st.fly=null; if(d) d(); } }
  else if(st.auto&&!interacting) st.lon+=dt*.035;
}
function wrap(a){ while(a>Math.PI) a-=TAU; while(a<-Math.PI) a+=TAU; return a; }
function fly(lonDeg,latDeg,zoom,ms,done){
  const l1=lonDeg*D2R, dl=wrap(l1-st.lon); st.fly={lon0:st.lon,lat0:st.lat,z0:st.zoom,lon1:st.lon+dl,lat1:latDeg*D2R,z1:zoom,t:0,dur:ms/1000,done}; st.auto=false;
}
window.Globe={
  st, draw, step, fly, radius, fromScreen, toView, regionMarker, hits:()=>hits,
  drag(dx,dy,W,H){ const R=radius(W,H); st.lon=wrap(st.lon-dx/R/Math.max(.35,Math.cos(st.lat))); st.lat=Math.max(-1.35,Math.min(1.35,st.lat+dy/R)); st.auto=false; st.fly=null; },
  zoom(dy){ st.zoom=Math.max(.6,Math.min(160,st.zoom*Math.exp(-dy*.0012))); },
  setView(lonDeg,latDeg,zoom){ st.lon=wrap(lonDeg*D2R); st.lat=latDeg*D2R; st.zoom=zoom; st.fly=null; },
  sunDeg(){ const s=subsolar(new Date()); return {lon:s.lon/D2R,lat:s.lat/D2R}; },
  stats(){ return {imgZ,lightsOk,patch,failed,maxTex}; }
};
})();
