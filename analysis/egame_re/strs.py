import re,sys
b=open('EGAME.bin','rb').read(); D=0x1ecf0
L=open('egame_all.asm').read().splitlines()
def s(o):
    e=b.find(b"\0",D+o); return b[D+o:min(e,D+o+60)].decode('latin1')
# string pushes followed within 6 lines by call to target
tgt=sys.argv[1]
for i,l in enumerate(L):
    if re.search(rf"call\s+0x(1|ffff)?0?{tgt}$", l):
        for j in range(i-1,max(i-8,0),-1):
            m=re.search(r"mov\s+ax, 0x([0-9a-f]{3,4})$",L[j])
            if m:
                o=int(m.group(1),16)
                if 0x3000<o<0xa000: print(l[:5], hex(o), repr(s(o))); break
