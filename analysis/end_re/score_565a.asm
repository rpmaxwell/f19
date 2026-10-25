565a: push   bp
565b: mov    bp, sp
565d: sub    sp, 0xc
5660: push   di
5661: push   si
5662: mov    word ptr [0x9aea], 0x1318
5668: sub    ax, ax
566a: mov    word ptr [0x9a0c], ax
566d: mov    word ptr [0x8ea4], ax
5670: sub    al, al
5672: mov    byte ptr [0x9dde], al
5675: mov    byte ptr [0x9a1b], al
5678: mov    byte ptr [0x99f5], al
567b: mov    byte ptr [0x978b], al
567e: mov    byte ptr [0x86c2], al
5681: mov    byte ptr [0x978a], al
5684: mov    byte ptr [0x86c1], al
5687: mov    byte ptr [0x917e], al
568a: mov    byte ptr [0x9986], al
568d: mov    word ptr [bp - 4], 0
5692: mov    word ptr [bp - 2], 0
5697: sub    ax, ax
5699: mov    word ptr [bp - 8], ax
569c: mov    word ptr [bp - 0xa], ax
569f: mov    word ptr [bp - 6], ax
56a2: jmp    0x5b55
56a5: cmp    word ptr [bp - 8], 0
56a9: jge    0x56b3
56ab: sub    ax, ax
56ad: mov    word ptr [bp - 8], ax
56b0: mov    word ptr [bp - 0xa], ax
56b3: cmp    word ptr [bp - 6], 0
56b7: jne    0x56bc
56b9: jmp    0x5b52
56bc: mov    word ptr [bp - 2], 1
56c1: jmp    0x5b52
56c4: mov    ax, 6
56c7: mul    word ptr [bp - 6]
56ca: mov    bx, ax
56cc: test   byte ptr [bx - 0x6e7a], 0x80
56d1: je     0x571d
56d3: les    bx, ptr [0x86ae]
56d7: mov    bx, word ptr es:[bx + 0x3a]
56db: shl    bx, 1
56dd: mov    ax, word ptr [bx + 0x4206]
56e1: cdq    
56e2: add    word ptr [bp - 0xa], ax
56e5: adc    word ptr [bp - 8], dx
56e8: mov    byte ptr [0x9a1b], 1
56ed: cmp    byte ptr [0x9dde], 1
56f2: je     0x56f7
56f4: jmp    0x5b52
56f7: mov    ax, word ptr [0x420c]
56fa: cdq    
56fb: sub    word ptr [bp - 0xa], ax
56fe: sbb    word ptr [bp - 8], dx
5701: mov    ax, word ptr [0x420e]
5704: cdq    
5705: add    word ptr [bp - 0xa], ax
5708: adc    word ptr [bp - 8], dx
570b: jmp    0x5b52
570e: mov    ax, 6
5711: mul    word ptr [bp - 6]
5714: mov    bx, ax
5716: test   byte ptr [bx - 0x6e7a], 0x80
571b: jne    0x56d3
571d: mov    ax, 6
5720: mul    word ptr [bp - 6]
5723: mov    bx, ax
5725: test   byte ptr [bx - 0x6e7a], 0x40
572a: jne    0x572f
572c: jmp    0x5b52
572f: mov    bl, byte ptr [0x9a1b]
5733: sub    bh, bh
5735: shl    bx, 1
5737: mov    ax, word ptr [bx + 0x420c]
573b: cdq    
573c: add    word ptr [bp - 0xa], ax
573f: adc    word ptr [bp - 8], dx
5742: mov    byte ptr [0x9dde], 1
5747: jmp    0x5b52
574a: mov    ax, 6
574d: mul    word ptr [bp - 6]
5750: mov    si, ax
5752: mov    di, word ptr [si - 0x6e7b]
5756: and    di, 0xff
575a: mov    cl, 4
575c: shr    di, cl
575e: shl    di, cl
5760: mov    bl, byte ptr [si - 0x6e7c]
5764: sub    bh, bh
5766: shr    bx, cl
5768: test   byte ptr [bx + di - 0x7486], 3
576d: je     0x5772
576f: jmp    0x5b52
5772: les    bx, ptr [0x86ae]
5776: cmp    word ptr es:[bx + 0x3a], 0
577b: jne    0x5788
577d: add    word ptr [bp - 0xa], 1
5781: adc    word ptr [bp - 8], 0
5785: jmp    0x5b52
5788: cmp    word ptr es:[bx + 0x3a], 1
578d: je     0x5792
578f: jmp    0x5b52
5792: inc    word ptr [bp - 4]
5795: test   byte ptr [bp - 4], 3
5799: je     0x579e
579b: jmp    0x5b52
579e: jmp    0x577d
57a0: mov    bx, word ptr [bp - 0xc]
57a3: mov    cl, 4
57a5: shl    bx, cl
57a7: test   word ptr [bx - 0x7932], 0x1000
57ad: je     0x57c8
57af: dec    word ptr [0x8ea4]
57b3: les    bx, ptr [0x86ae]
57b7: mov    bx, word ptr es:[bx + 0x3a]
57bb: shl    bx, 1
57bd: mov    ax, word ptr [bx + 0x4210]
57c1: cdq    
57c2: sub    word ptr [bp - 0xa], ax
57c5: sbb    word ptr [bp - 8], dx
57c8: mov    ax, 6
57cb: mul    word ptr [bp - 6]
57ce: mov    bx, ax
57d0: test   byte ptr [bx - 0x6e7a], 0x80
57d5: je     0x5812
57d7: les    bx, ptr [0x86ae]
57db: mov    bx, word ptr es:[bx + 0x3a]
57df: shl    bx, 1
57e1: mov    ax, word ptr [bx + 0x4206]
57e5: cdq    
57e6: add    word ptr [bp - 0xa], ax
57e9: adc    word ptr [bp - 8], dx
57ec: mov    byte ptr [0x9a1b], 1
57f1: cmp    byte ptr [0x9dde], 1
57f6: je     0x57fb
57f8: jmp    0x5a52
57fb: mov    ax, word ptr [0x420c]
57fe: cdq    
57ff: sub    word ptr [bp - 0xa], ax
5802: sbb    word ptr [bp - 8], dx
5805: mov    ax, word ptr [0x420e]
5808: cdq    
5809: add    word ptr [bp - 0xa], ax
580c: adc    word ptr [bp - 8], dx
580f: jmp    0x5a52
5812: mov    ax, 6
5815: mul    word ptr [bp - 6]
5818: mov    bx, ax
581a: test   byte ptr [bx - 0x6e7a], 0x40
581f: je     0x582a
5821: mov    bl, byte ptr [0x9a1b]
5825: sub    bh, bh
5827: jmp    0x5a40
582a: mov    bx, word ptr [bp - 0xc]
582d: and    bx, 0x7f
5830: test   byte ptr [bx - 0x6676], 0x40
5835: je     0x583a
5837: jmp    0x5a66
583a: mov    bx, word ptr [bp - 0xc]
583d: mov    cl, 4
583f: shl    bx, cl
5841: mov    si, word ptr [bx - 0x7932]
5845: test   si, 0x500
5849: je     0x584e
584b: jmp    0x5a66
584e: test   si, 0x1000
5852: je     0x585f
5854: les    bx, ptr [0x86ae]
5858: cmp    word ptr es:[bx + 0x3a], 0
585d: je     0x586a
585f: les    bx, ptr [0x86ae]
5863: cmp    word ptr es:[bx + 0x3a], 0
5868: je     0x5876
586a: mov    bx, word ptr es:[bx + 0x3a]
586e: shl    bx, 1
5870: mov    ax, word ptr [bx + 0x4216]
5874: jmp    0x5808
5876: les    bx, ptr [0x86ae]
587a: mov    bx, word ptr es:[bx + 0x3a]
587e: shl    bx, 1
5880: mov    ax, word ptr [bx + 0x4228]
5884: cdq    
5885: add    word ptr [bp - 0xa], ax
5888: adc    word ptr [bp - 8], dx
588b: mov    bx, word ptr [0x86ae]
588f: mov    bx, word ptr es:[bx + 0x3a]
5893: shl    bx, 1
5895: cmp    word ptr [bx + 0x4228], 0
589a: jne    0x589f
589c: jmp    0x5b52
589f: inc    byte ptr [0x917e]
58a3: jmp    0x5b52
58a6: test   byte ptr [bp - 0xc], 0x80
58aa: je     0x58c5
58ac: dec    word ptr [0x9a0c]
58b0: les    bx, ptr [0x86ae]
58b4: mov    bx, word ptr es:[bx + 0x3a]
58b8: shl    bx, 1
58ba: mov    ax, word ptr [bx + 0x4234]
58be: cdq    
58bf: sub    word ptr [bp - 0xa], ax
58c2: sbb    word ptr [bp - 8], dx
58c5: mov    ax, 6
58c8: mul    word ptr [bp - 6]
58cb: mov    bx, ax
58cd: test   byte ptr [bx - 0x6e7a], 0x80
58d2: je     0x5908
58d4: les    bx, ptr [0x86ae]
58d8: mov    bx, word ptr es:[bx + 0x3a]
58dc: shl    bx, 1
58de: mov    ax, word ptr [bx + 0x4206]
58e2: cdq    
58e3: add    word ptr [bp - 0xa], ax
58e6: adc    word ptr [bp - 8], dx
58e9: mov    byte ptr [0x9a1b], 1
58ee: cmp    byte ptr [0x9dde], 1
58f3: je     0x58f8
58f5: jmp    0x59db
58f8: mov    ax, word ptr [0x420c]
58fb: cdq    
58fc: sub    word ptr [bp - 0xa], ax
58ff: sbb    word ptr [bp - 8], dx
5902: mov    ax, word ptr [0x420e]
5905: jmp    0x59d4
5908: mov    ax, 6
590b: mul    word ptr [bp - 6]
590e: mov    bx, ax
5910: test   byte ptr [bx - 0x6e7a], 0x40
5915: je     0x5932
5917: mov    bl, byte ptr [0x9a1b]
591b: sub    bh, bh
591d: shl    bx, 1
591f: mov    ax, word ptr [bx + 0x420c]
5923: cdq    
5924: add    word ptr [bp - 0xa], ax
5927: adc    word ptr [bp - 8], dx
592a: mov    byte ptr [0x9dde], 1
592f: jmp    0x59db
5932: mov    bx, word ptr [bp - 0xc]
5935: and    bx, 0x7f
5938: mov    cl, 5
593a: shl    bx, cl
593c: cmp    word ptr [bx + 0x1b0], -1
5941: jne    0x595f
5943: les    bx, ptr [0x86ae]
5947: mov    bx, word ptr es:[bx + 0x3a]
594b: shl    bx, 1
594d: mov    ax, word ptr [bx + 0x424c]
5951: cdq    
5952: add    word ptr [bp - 0xa], ax
5955: adc    word ptr [bp - 8], dx
5958: inc    byte ptr [0x86c2]
595c: jmp    0x5b52
595f: test   byte ptr [bp - 0xc], 0x80
5963: je     0x5970
5965: les    bx, ptr [0x86ae]
5969: cmp    word ptr es:[bx + 0x3a], 0
596e: je     0x597b
5970: les    bx, ptr [0x86ae]
5974: cmp    word ptr es:[bx + 0x3a], 0
5979: je     0x59e2
597b: mov    bx, word ptr [bp - 0xc]
597e: and    bx, 0x7f
5981: mov    cl, 5
5983: shl    bx, cl
5985: cmp    word ptr [bx + 0x1b0], 0
598a: jne    0x599c
598c: mov    bx, word ptr [0x86ae]
5990: mov    bx, word ptr es:[bx + 0x3a]
5994: shl    bx, 1
5996: mov    ax, word ptr [bx + 0x4246]
599a: jmp    0x59d4
599c: mov    bx, word ptr [bp - 0xc]
599f: and    bx, 0x7f
59a2: mov    cl, 5
59a4: shl    bx, cl
59a6: mov    ax, 0xe
59a9: imul   word ptr [bx + 0x1b0]
59ad: mov    bx, ax
59af: test   byte ptr [bx + 0x62], 4
59b4: je     0x59c6
59b6: les    bx, ptr [0x86ae]
59ba: mov    bx, word ptr es:[bx + 0x3a]
59be: shl    bx, 1
59c0: mov    ax, word ptr [bx + 0x423a]
59c4: jmp    0x59d4
59c6: les    bx, ptr [0x86ae]
59ca: mov    bx, word ptr es:[bx + 0x3a]
59ce: shl    bx, 1
59d0: mov    ax, word ptr [bx + 0x4240]
59d4: cdq    
59d5: add    word ptr [bp - 0xa], ax
59d8: adc    word ptr [bp - 8], dx
59db: inc    byte ptr [0x86c1]
59df: jmp    0x5b52
59e2: les    bx, ptr [0x86ae]
59e6: mov    bx, word ptr es:[bx + 0x3a]
59ea: shl    bx, 1
59ec: mov    ax, word ptr [bx + 0x4252]
59f0: cdq    
59f1: add    word ptr [bp - 0xa], ax
59f4: adc    word ptr [bp - 8], dx
59f7: inc    byte ptr [0x9986]
59fb: jmp    0x5b52
59fe: mov    ax, 6
5a01: mul    word ptr [bp - 6]
5a04: mov    bx, ax
5a06: test   byte ptr [bx - 0x6e7a], 0x80
5a0b: je     0x5a29
5a0d: les    bx, ptr [0x86ae]
5a11: mov    bx, word ptr es:[bx + 0x3a]
5a15: shl    bx, 1
5a17: mov    ax, word ptr [bx + 0x4206]
5a1b: cdq    
5a1c: add    word ptr [bp - 0xa], ax
5a1f: adc    word ptr [bp - 8], dx
5a22: mov    byte ptr [0x9a1b], 1
5a27: jmp    0x5a52
5a29: mov    ax, 6
5a2c: mul    word ptr [bp - 6]
5a2f: mov    bx, ax
5a31: test   byte ptr [bx - 0x6e7a], 0x40
5a36: je     0x5a59
5a38: les    bx, ptr [0x86ae]
5a3c: mov    bx, word ptr es:[bx + 0x3a]
5a40: shl    bx, 1
5a42: mov    ax, word ptr [bx + 0x420c]
5a46: cdq    
5a47: add    word ptr [bp - 0xa], ax
5a4a: adc    word ptr [bp - 8], dx
5a4d: mov    byte ptr [0x9dde], 1
5a52: inc    byte ptr [0x978a]
5a56: jmp    0x5b52
5a59: mov    bx, word ptr [bp - 0xc]
5a5c: and    bx, 0x7f
5a5f: test   byte ptr [bx - 0x6676], 0x40
5a64: je     0x5a82
5a66: les    bx, ptr [0x86ae]
5a6a: mov    bx, word ptr es:[bx + 0x3a]
5a6e: shl    bx, 1
5a70: mov    ax, word ptr [bx + 0x422e]
5a74: cdq    
5a75: add    word ptr [bp - 0xa], ax
5a78: adc    word ptr [bp - 8], dx
5a7b: inc    byte ptr [0x978b]
5a7f: jmp    0x5b52
5a82: mov    ax, 6
5a85: mul    word ptr [bp - 6]
5a88: mov    si, ax
5a8a: mov    di, word ptr [si - 0x6e7b]
5a8e: and    di, 0xff
5a92: mov    cl, 4
5a94: shr    di, cl
5a96: shl    di, cl
5a98: mov    bl, byte ptr [si - 0x6e7c]
5a9c: sub    bh, bh
5a9e: shr    bx, cl
5aa0: test   byte ptr [bx + di - 0x7486], 3
5aa5: jne    0x5a66
5aa7: mov    bx, word ptr [bp - 0xc]
5aaa: and    bx, 0x7f
5aad: test   byte ptr [bx - 0x6676], 0x80
5ab2: je     0x5ad0
5ab4: les    bx, ptr [0x86ae]
5ab8: mov    bx, word ptr es:[bx + 0x3a]
5abc: shl    bx, 1
5abe: mov    ax, word ptr [bx + 0x4222]
5ac2: cdq    
5ac3: add    word ptr [bp - 0xa], ax
5ac6: adc    word ptr [bp - 8], dx
5ac9: inc    byte ptr [0x99f5]
5acd: jmp    0x5b52
5ad0: les    bx, ptr [0x86ae]
5ad4: mov    bx, word ptr es:[bx + 0x3a]
5ad8: mov    cl, 5
5ada: shl    bx, cl
5adc: mov    si, word ptr [bp - 0xc]
5adf: and    si, 0x7f
5ae2: mov    al, byte ptr [si - 0x6676]
5ae6: cwde   
5ae7: mov    si, ax
5ae9: and    si, 0xf
5aec: shl    si, 1
5aee: mov    ax, word ptr [bx + si + 0x4258]
5af2: cdq    
5af3: add    word ptr [bp - 0xa], ax
5af6: adc    word ptr [bp - 8], dx
5af9: mov    bx, word ptr [0x86ae]
5afd: mov    bx, word ptr es:[bx + 0x3a]
5b01: shl    bx, 1
5b03: cmp    word ptr [bx + 0x421c], 0
5b08: jl     0x5b0d
5b0a: jmp    0x5a52
5b0d: jmp    0x589f
5b10: inc    word ptr [0x8ea4]
5b14: les    bx, ptr [0x86ae]
5b18: mov    bx, word ptr es:[bx + 0x3a]
5b1c: shl    bx, 1
5b1e: mov    ax, word ptr [bx + 0x4210]
5b22: jmp    0x5704
5b25: inc    word ptr [0x9a0c]
5b29: les    bx, ptr [0x86ae]
5b2d: mov    bx, word ptr es:[bx + 0x3a]
5b31: shl    bx, 1
5b33: mov    ax, word ptr [bx + 0x4234]
5b37: jmp    0x5704
5b3a: lodsb  al, byte ptr [si]
5b3b: push   di
5b3c: or     bl, byte ptr [bp + si - 0x4e]
5b3f: pop    ax
5b40: pop    si
5b41: pop    bx
5b42: pop    si
5b43: pop    bx
5b44: xor    word ptr [bp + di + 0x1c], bx
5b47: pop    bx
5b48: mov    cl, 0x56
5b4a: push   si
5b4b: push   di
5b4c: rcl    byte ptr [bp + 0x1a]
5b4f: push   di
5b50: lodsb  al, byte ptr [si]
5b51: push   di
5b52: inc    word ptr [bp - 6]
5b55: mov    ax, word ptr [bp + 4]
5b58: cmp    word ptr [bp - 6], ax
5b5b: ja     0x5b8b
5b5d: mov    ax, 6
5b60: mul    word ptr [bp - 6]
5b63: mov    si, ax
5b65: cmp    byte ptr [si - 0x6e7a], 0
5b6a: je     0x5b8b
5b6c: mov    al, byte ptr [si - 0x6e79]
5b70: cwde   
5b71: mov    word ptr [bp - 0xc], ax
5b74: mov    al, byte ptr [si - 0x6e7a]
5b78: and    ax, 0x3f
5b7b: sub    ax, 1
5b7e: cmp    ax, 0xb
5b81: ja     0x5b52
5b83: add    ax, ax
5b85: xchg   bx, ax
5b86: jmp    word ptr cs:[bx + 0x5b46]
5b8b: mov    ax, 8
5b8e: cdq    
5b8f: push   dx
5b90: push   ax
5b91: push   word ptr [bp - 8]
5b94: push   word ptr [bp - 0xa]
5b97: les    bx, ptr [0x86ae]
5b9b: mov    bx, word ptr es:[bx + 0x38]
5b9f: shl    bx, 1
5ba1: mov    ax, word ptr [bx + 0x42b8]
5ba5: cdq    
5ba6: push   dx
5ba7: push   ax
5ba8: call   0x8d4c
5bab: push   dx
5bac: push   ax
5bad: call   0x8cb0
5bb0: mov    word ptr [bp - 0xa], ax
5bb3: mov    word ptr [bp - 8], dx
5bb6: mov    ax, 8
5bb9: cdq    
5bba: push   dx
5bbb: push   ax
5bbc: push   word ptr [bp - 8]
5bbf: push   word ptr [bp - 0xa]
5bc2: les    bx, ptr [0x86ae]
5bc6: mov    bx, word ptr es:[bx + 0x3c]
5bca: shl    bx, 1
5bcc: mov    ax, word ptr [bx + 0x42c2]
5bd0: cdq    
5bd1: push   dx
5bd2: push   ax
5bd3: call   0x8d4c
5bd6: push   dx
5bd7: push   ax
5bd8: call   0x8cb0
5bdb: mov    word ptr [bp - 0xa], ax
5bde: mov    word ptr [bp - 8], dx
5be1: mov    ax, 8
5be4: cdq    
5be5: push   dx
5be6: push   ax
5be7: push   word ptr [bp - 8]
5bea: push   word ptr [bp - 0xa]
5bed: les    bx, ptr [0x86ae]
5bf1: mov    bx, word ptr es:[bx + 0x3e]
5bf5: shl    bx, 1
5bf7: mov    ax, word ptr [bx + 0x42ca]
5bfb: cdq    
5bfc: push   dx
5bfd: push   ax
5bfe: call   0x8d4c
5c01: push   dx
5c02: push   ax
5c03: call   0x8cb0
5c06: mov    word ptr [bp - 0xa], ax
5c09: mov    word ptr [bp - 8], dx
5c0c: mov    ax, 8
5c0f: cdq    
5c10: push   dx
5c11: push   ax
5c12: push   word ptr [bp - 8]
5c15: push   word ptr [bp - 0xa]
5c18: les    bx, ptr [0x86ae]
5c1c: mov    bx, word ptr es:[bx + 0x40]
5c20: shl    bx, 1
5c22: mov    ax, word ptr [bx + 0x42d2]
5c26: cdq    
5c27: push   dx
5c28: push   ax
5c29: call   0x8d4c
5c2c: push   dx
5c2d: push   ax
5c2e: call   0x8cb0
5c31: mov    word ptr [bp - 0xa], ax
5c34: mov    word ptr [bp - 8], dx
5c37: cmp    word ptr [bp - 2], 1
5c3b: jne    0x5c6f
5c3d: les    bx, ptr [0x9ed6]
5c41: cmp    word ptr es:[bx + 0x26], 2
5c46: jne    0x5c6f
5c48: mov    ax, 8
5c4b: cdq    
5c4c: push   dx
5c4d: push   ax
5c4e: push   word ptr [bp - 8]
5c51: push   word ptr [bp - 0xa]
5c54: mov    bx, word ptr [0x978c]
5c58: shl    bx, 1
5c5a: mov    ax, word ptr [bx + 0x42d8]
5c5e: cdq    
5c5f: push   dx
5c60: push   ax
5c61: call   0x8d4c
5c64: push   dx
5c65: push   ax
5c66: call   0x8cb0
5c69: mov    word ptr [bp - 0xa], ax
5c6c: mov    word ptr [bp - 8], dx
5c6f: les    bx, ptr [0x9ed6]
5c73: cmp    word ptr es:[bx + 0x2e], 0x1b58
5c79: jb     0x5ca3
5c7b: cmp    word ptr es:[bx + 0x2e], 0x2328
5c81: jae    0x5ca3
5c83: mov    ax, 8
5c86: cdq    
5c87: push   dx
5c88: push   ax
5c89: mov    ax, 9
5c8c: cdq    
5c8d: push   dx
5c8e: push   ax
5c8f: push   word ptr [bp - 8]
5c92: push   word ptr [bp - 0xa]
5c95: call   0x8d4c
5c98: push   dx
5c99: push   ax
5c9a: call   0x8cb0
5c9d: mov    word ptr [bp - 0xa], ax
5ca0: mov    word ptr [bp - 8], dx
5ca3: les    bx, ptr [0x9ed6]
5ca7: cmp    word ptr es:[bx + 0x2e], 0x2328
5cad: jb     0x5cd7
5caf: cmp    word ptr es:[bx + 0x2e], 0x2cec
5cb5: jae    0x5cd7
5cb7: mov    ax, 8
5cba: cdq    
5cbb: push   dx
5cbc: push   ax
5cbd: mov    ax, 0xa
5cc0: cdq    
5cc1: push   dx
5cc2: push   ax
5cc3: push   word ptr [bp - 8]
5cc6: push   word ptr [bp - 0xa]
5cc9: call   0x8d4c
5ccc: push   dx
5ccd: push   ax
5cce: call   0x8cb0
5cd1: mov    word ptr [bp - 0xa], ax
5cd4: mov    word ptr [bp - 8], dx
5cd7: les    bx, ptr [0x9ed6]
5cdb: cmp    word ptr es:[bx + 0x2e], 0x2cec
5ce1: jb     0x5d03
5ce3: mov    ax, 8
5ce6: cdq    
5ce7: push   dx
5ce8: push   ax
5ce9: mov    ax, 0xb
5cec: cdq    
5ced: push   dx
5cee: push   ax
5cef: push   word ptr [bp - 8]
5cf2: push   word ptr [bp - 0xa]
5cf5: call   0x8d4c
5cf8: push   dx
5cf9: push   ax
5cfa: call   0x8cb0
5cfd: mov    word ptr [bp - 0xa], ax
5d00: mov    word ptr [bp - 8], dx
5d03: mov    ax, word ptr [bp - 0xa]
5d06: mov    dx, word ptr [bp - 8]
5d09: pop    si
5d0a: pop    di
5d0b: mov    sp, bp
5d0d: pop    bp
5d0e: ret    
