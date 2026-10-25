"""Group ring frames (radar_rings.py) into pulses, assign radars, join with radar_blinks.csv."""
import csv, json, math, numpy as np, collections, sys
sys.path.insert(0,'analysis')
S='/private/tmp/claude-501/-Users-robmaxwell-projects-f19/3a0ee1a5-d413-4b8e-bbd7-1f2111a92149/scratchpad/'
R=list(csv.DictReader(open(S+'ring_events.csv')))
M={r['place']+' '+r['system']:r for r in json.load(open('logs/plan/ce_cottbus/mission_mem.json'))['radars']}
ev=[]
for r in R:
    t=float(r['t'])
    if ev and t-ev[-1][-1]['tt']<1.3: r['tt']=t; ev[-1].append(r)
    else: r['tt']=t; ev.append([r])
P=[]
for e in ev:
    good=[r for r in e if float(r['fit_km'])<4] or e
    cx=np.median([float(r['cx']) for r in good]); cy=np.median([float(r['cy']) for r in good])
    d=sorted((math.hypot(cx-m['x'],cy-m['y'])/64,k) for k,m in M.items())
    P.append(dict(t=e[0]['tt'],el=float(e[0]['el']),frames=len(e),zoom=e[0]['zoom'],radar=d[0][1] if d[0][0]<25 else '?',err=round(d[0][0],1),cx=cx,cy=cy,maxR=max(float(r['R_km']) for r in e)))
B=list(csv.DictReader(open('logs/plan/ce_cottbus/radar_blinks.csv')))
for b in B: b['t0']=float(b['t0'])
used=set()
out=[]
for p in P:
    c=[b for b in B if -1.0<=b['t0']-p['t']<=1.5]
    b=min(c,key=lambda b:abs(b['t0']-p['t'])) if c else None
    if b: used.add(b['t0'])
    out.append((p,b))
multi=[p for p in P if p['frames']>=2]
print('pulse events',len(P),' multi-frame',len(multi))
print('by radar:',collections.Counter(p['radar'] for p in multi))
# periods
for k in set(p['radar'] for p in multi):
    tt=[p['t'] for p in multi if p['radar']==k]; dd=np.diff(tt)
    print(k, 'intervals', np.round(dd[dd<60],1))
print()
print('multi-frame pulses: blink match')
for p,b in out:
    if p['frames']<2: continue
    m=M.get(p['radar']); 
    print(f"{p['el']:7.1f} {p['radar']:20s} err {p['err']:5.1f} z{p['zoom']}  ->", f"blink dt {b['t0']-p['t']:+.2f} rows {b['rows']:>2} sev {b['sev']} emv {b['emv']}" if b else 'NO BLINK')
nb=[b for b in B if b['t0'] not in used]
print('blinks without a ring', len(nb), 'of', len(B))
json.dump([dict(p, blink=(dict(t0=b['t0'],rows=int(b['rows']),sev=int(b['sev']),emv=b['emv'],x=b['x'],y=b['y'],hdg=b['hdg']) if b else None)) for p,b in out], open(S+'pulses.json','w'), indent=0, default=float)
