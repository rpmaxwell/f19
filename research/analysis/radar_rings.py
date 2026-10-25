"""Radar pulse rings on the cockpit MAP display (left screen), from emv_logger panel frames.

Each pulse is drawn as an expanding pink (236,98,92) double ellipse centred on the radar.
New pink pixels per frame are converted to game units (map scale 0.237 px/km x, 0.70 y/x ratio
at zoom 1; x4 / x8 when zoomed; offset from the plane marker) and a circle fit gives the source.
Hard-coded to the 2026-10-16 ce_cottbus flight (logs/emv/1792199394, nav 1792199506);
writes <scratch>/ring_events.csv. Then radar_rings_join.py groups frames into pulses and joins blinks.
"""
import csv, numpy as np, json, math
from PIL import Image
from pathlib import Path
S='/private/tmp/claude-501/-Users-robmaxwell-projects-f19/3a0ee1a5-d413-4b8e-bbd7-1f2111a92149/scratchpad/'
D=Path('logs/emv/1792199394')
M=json.load(open('logs/plan/ce_cottbus/mission_mem.json'))['radars']
nav=[r for r in csv.DictReader(open('logs/nav/1792199506.csv')) if r['x'] and r['alt'] and int(r['alt'])>150]
NT=np.array([float(r['t']) for r in nav]); NX=np.array([float(r['x']) for r in nav]); NY=np.array([float(r['y']) for r in nav]); NH=np.array([float(r['hdg']) for r in nav])
t0=NT[0]
SEG=[(0,497,1),(498,1078,None),(1079,1330,4)]
fs=[f for f in sorted(D.glob('*.png')) if t0<=float(f.stem)<=NT[-1]]
ts=np.array([float(f.stem) for f in fs])
load=lambda f: np.asarray(Image.open(f).convert('RGB'))[18:99,40:146].astype(int)
PINK=(236,98,92)
cache={}
def img(i):
    if i not in cache: cache[i]=load(fs[i])
    return cache[i]
def zoom_at(t):
    a=img(int(np.searchsorted(ts,t)))
    sea=(np.abs(a-(0,0,163)).sum(2)<10).sum()
    return None
def offset(i,sx,sy):
    """offset from the plane marker (moving white pixels) in frames i-40..i+40"""
    lo,hi=max(0,i-40),min(len(fs),i+40)
    W=np.array([(np.abs(img(k)-255).sum(2)<10) for k in range(lo,hi)]); freq=W.mean(0)
    O=[]
    for k,w in zip(range(lo,hi),W):
        ys,xs=np.nonzero(w&(freq<0.3)); g=(np.interp(ts[k],NT,NX),np.interp(ts[k],NT,NY))
        O+= [(x-sx*g[0], y-sy*g[1]) for x,y in zip(xs,ys)]
    O=np.array(O)
    if len(O)<5: return None
    best=max(O, key=lambda o: (np.hypot(*(O-o).T)<1.5).sum())
    inl=O[np.hypot(*(O-best).T)<1.5]; return inl.mean(0), len(inl)/len(O)
def fit_circle(x,y):
    A=np.c_[2*x,2*y,np.ones(len(x))]; b=x**2+y**2
    (cx,cy,c),*_=np.linalg.lstsq(A,b,rcond=None); return cx,cy,math.sqrt(max(c+cx**2+cy**2,0))
rows=[]
prevpink=None
for i in range(len(fs)):
    a=img(i)
    pk=(np.abs(a-PINK).sum(2)<10)
    if prevpink is not None:
        new=pk&~prevpink
        if new.sum()>=12:
            t=ts[i]; el=t-t0
            sea=(np.abs(a-(0,0,163)).sum(2)<10).sum()
            z=1 if sea>2000 else (8 if el<740 and el>498 else 4)
            sx=0.237/64*z; sy=sx*0.70
            if z==1: o,q=np.array([0.5,10.6]),1.0
            else:
                r=offset(i,sx,sy)
                if r is None: prevpink=pk; continue
                o,q=r
            ys,xs=np.nonzero(new)
            gx=(xs-o[0])/sx; gy=(ys-o[1])/sy                      # game units
            cx,cy,R=fit_circle(gx,gy)
            res=np.abs(np.hypot(gx-cx,gy-cy)-R).mean()
            px,py=np.interp(t,NT,NX),np.interp(t,NT,NY)
            d=sorted((math.hypot(cx-r['x'],cy-r['y'])/64, r['place']+' '+r['system'], math.hypot(px-r['x'],py-r['y'])/64) for r in M)
            rows.append(dict(t=round(t,2),el=round(el,1),zoom=z,n=int(new.sum()),cx=round(cx),cy=round(cy),R_km=round(R/64,1),fit_km=round(res/64,1),
                             radar=d[0][1],err_km=round(d[0][0],1),radar_dist_km=round(d[0][2]),second=d[1][1],second_err=round(d[1][0],1),oq=round(q,2)))
    prevpink=pk
    if len(cache)>200: cache={k:v for k,v in cache.items() if k>i-60}
w=csv.DictWriter(open(S+'ring_events.csv','w'),fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(len(rows))
