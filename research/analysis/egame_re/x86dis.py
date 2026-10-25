import sys, capstone
data = open(sys.argv[1],'rb').read()
start = int(sys.argv[2],16); n = int(sys.argv[3])
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_16)
for i,ins in enumerate(md.disasm(data[start:start+n*8], start)):
    if i>=n: break
    print(f"{ins.address:05x}  {ins.mnemonic:6} {ins.op_str}")
