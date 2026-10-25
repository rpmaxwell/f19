5666: push   bp
5667: mov    bp, sp
5669: sub    sp, 0xc
566c: push   di
566d: push   si
566e: mov    word ptr [0x9aea], 0x1318
5674: sub    ax, ax
5676: mov    word ptr [0x9a0c], ax
5679: mov    word ptr [0x8ea4], ax
567c: sub    al, al
567e: mov    byte ptr [0x9dde], al
5681: mov    byte ptr [0x9a1b], al
5684: mov    byte ptr [0x99f5], al
5687: mov    byte ptr [0x978b], al
568a: mov    byte ptr [0x86c2], al
568d: mov    byte ptr [0x978a], al
5690: mov    byte ptr [0x86c1], al
5693: mov    byte ptr [0x917e], al
5696: mov    byte ptr [0x9986], al
5699: mov    word ptr [bp - 4], 0
569e: mov    word ptr [bp - 2], 0
56a3: sub    ax, ax
56a5: mov    word ptr [bp - 8], ax
56a8: mov    word ptr [bp - 0xa], ax
56ab: mov    word ptr [bp - 6], ax
56ae: jmp    0x5b61
56b1: cmp    word ptr [bp - 8], 0
56b5: jge    0x56bf
56b7: sub    ax, ax
56b9: mov    word ptr [bp - 8], ax
56bc: mov    word ptr [bp - 0xa], ax
56bf: cmp    word ptr [bp - 6], 0
56c3: jne    0x56c8
56c5: jmp    0x5b5e
56c8: mov    word ptr [bp - 2], 1
56cd: jmp    0x5b5e
56d0: mov    ax, 6
56d3: mul    word ptr [bp - 6]
56d6: mov    bx, ax
56d8: test   byte ptr [bx - 0x6e7a], 0x80
56dd: je     0x5729
56df: les    bx, ptr [0x86ae]
56e3: mov    bx, word ptr es:[bx + 0x3a]
56e7: shl    bx, 1
56e9: mov    ax, word ptr [bx + 0x4206]
56ed: cdq    
56ee: add    word ptr [bp - 0xa], ax
56f1: adc    word ptr [bp - 8], dx
56f4: mov    byte ptr [0x9a1b], 1
56f9: cmp    byte ptr [0x9dde], 1
56fe: je     0x5703
5700: jmp    0x5b5e
5703: mov    ax, word ptr [0x420c]
5706: cdq    
5707: sub    word ptr [bp - 0xa], ax
570a: sbb    word ptr [bp - 8], dx
570d: mov    ax, word ptr [0x420e]
5710: cdq    
5711: add    word ptr [bp - 0xa], ax
5714: adc    word ptr [bp - 8], dx
5717: jmp    0x5b5e
571a: mov    ax, 6
571d: mul    word ptr [bp - 6]
5720: mov    bx, ax
5722: test   byte ptr [bx - 0x6e7a], 0x80
5727: jne    0x56df
5729: mov    ax, 6
572c: mul    word ptr [bp - 6]
572f: mov    bx, ax
5731: test   byte ptr [bx - 0x6e7a], 0x40
5736: jne    0x573b
5738: jmp    0x5b5e
573b: mov    bl, byte ptr [0x9a1b]
573f: sub    bh, bh
5741: shl    bx, 1
5743: mov    ax, word ptr [bx + 0x420c]
5747: cdq    
5748: add    word ptr [bp - 0xa], ax
574b: adc    word ptr [bp - 8], dx
574e: mov    byte ptr [0x9dde], 1
5753: jmp    0x5b5e
5756: mov    ax, 6
5759: mul    word ptr [bp - 6]
575c: mov    si, ax
575e: mov    di, word ptr [si - 0x6e7b]
5762: and    di, 0xff
5766: mov    cl, 4
5768: shr    di, cl
576a: shl    di, cl
576c: mov    bl, byte ptr [si - 0x6e7c]
5770: sub    bh, bh
5772: shr    bx, cl
5774: test   byte ptr [bx + di - 0x7486], 3
5779: je     0x577e
577b: jmp    0x5b5e
577e: les    bx, ptr [0x86ae]
5782: cmp    word ptr es:[bx + 0x3a], 0
5787: jne    0x5794
5789: add    word ptr [bp - 0xa], 1
578d: adc    word ptr [bp - 8], 0
5791: jmp    0x5b5e
5794: cmp    word ptr es:[bx + 0x3a], 1
5799: je     0x579e
579b: jmp    0x5b5e
579e: inc    word ptr [bp - 4]
57a1: test   byte ptr [bp - 4], 3
57a5: je     0x57aa
57a7: jmp    0x5b5e
57aa: jmp    0x5789
57ac: mov    bx, word ptr [bp - 0xc]
57af: mov    cl, 4
57b1: shl    bx, cl
57b3: test   word ptr [bx - 0x7932], 0x1000
57b9: je     0x57d4
57bb: dec    word ptr [0x8ea4]
57bf: les    bx, ptr [0x86ae]
57c3: mov    bx, word ptr es:[bx + 0x3a]
57c7: shl    bx, 1
57c9: mov    ax, word ptr [bx + 0x4210]
57cd: cdq    
57ce: sub    word ptr [bp - 0xa], ax
57d1: sbb    word ptr [bp - 8], dx
57d4: mov    ax, 6
57d7: mul    word ptr [bp - 6]
57da: mov    bx, ax
57dc: test   byte ptr [bx - 0x6e7a], 0x80
57e1: je     0x581e
57e3: les    bx, ptr [0x86ae]
57e7: mov    bx, word ptr es:[bx + 0x3a]
57eb: shl    bx, 1
57ed: mov    ax, word ptr [bx + 0x4206]
57f1: cdq    
57f2: add    word ptr [bp - 0xa], ax
57f5: adc    word ptr [bp - 8], dx
57f8: mov    byte ptr [0x9a1b], 1
57fd: cmp    byte ptr [0x9dde], 1
5802: je     0x5807
5804: jmp    0x5a5e
5807: mov    ax, word ptr [0x420c]
580a: cdq    
580b: sub    word ptr [bp - 0xa], ax
580e: sbb    word ptr [bp - 8], dx
5811: mov    ax, word ptr [0x420e]
5814: cdq    
5815: add    word ptr [bp - 0xa], ax
5818: adc    word ptr [bp - 8], dx
581b: jmp    0x5a5e
581e: mov    ax, 6
5821: mul    word ptr [bp - 6]
5824: mov    bx, ax
5826: test   byte ptr [bx - 0x6e7a], 0x40
582b: je     0x5836
582d: mov    bl, byte ptr [0x9a1b]
5831: sub    bh, bh
5833: jmp    0x5a4c
5836: mov    bx, word ptr [bp - 0xc]
5839: and    bx, 0x7f
583c: test   byte ptr [bx - 0x6676], 0x40
5841: je     0x5846
5843: jmp    0x5a72
5846: mov    bx, word ptr [bp - 0xc]
5849: mov    cl, 4
584b: shl    bx, cl
584d: mov    si, word ptr [bx - 0x7932]
5851: test   si, 0x500
5855: je     0x585a
5857: jmp    0x5a72
585a: test   si, 0x1000
585e: je     0x586b
5860: les    bx, ptr [0x86ae]
5864: cmp    word ptr es:[bx + 0x3a], 0
5869: je     0x5876
586b: les    bx, ptr [0x86ae]
586f: cmp    word ptr es:[bx + 0x3a], 0
5874: je     0x5882
5876: mov    bx, word ptr es:[bx + 0x3a]
587a: shl    bx, 1
587c: mov    ax, word ptr [bx + 0x4216]
5880: jmp    0x5814
5882: les    bx, ptr [0x86ae]
5886: mov    bx, word ptr es:[bx + 0x3a]
588a: shl    bx, 1
588c: mov    ax, word ptr [bx + 0x4228]
5890: cdq    
5891: add    word ptr [bp - 0xa], ax
5894: adc    word ptr [bp - 8], dx
5897: mov    bx, word ptr [0x86ae]
589b: mov    bx, word ptr es:[bx + 0x3a]
589f: shl    bx, 1
58a1: cmp    word ptr [bx + 0x4228], 0
58a6: jne    0x58ab
58a8: jmp    0x5b5e
58ab: inc    byte ptr [0x917e]
58af: jmp    0x5b5e
58b2: test   byte ptr [bp - 0xc], 0x80
58b6: je     0x58d1
58b8: dec    word ptr [0x9a0c]
58bc: les    bx, ptr [0x86ae]
58c0: mov    bx, word ptr es:[bx + 0x3a]
58c4: shl    bx, 1
58c6: mov    ax, word ptr [bx + 0x4234]
58ca: cdq    
58cb: sub    word ptr [bp - 0xa], ax
58ce: sbb    word ptr [bp - 8], dx
58d1: mov    ax, 6
58d4: mul    word ptr [bp - 6]
58d7: mov    bx, ax
58d9: test   byte ptr [bx - 0x6e7a], 0x80
58de: je     0x5914
58e0: les    bx, ptr [0x86ae]
58e4: mov    bx, word ptr es:[bx + 0x3a]
58e8: shl    bx, 1
58ea: mov    ax, word ptr [bx + 0x4206]
58ee: cdq    
58ef: add    word ptr [bp - 0xa], ax
58f2: adc    word ptr [bp - 8], dx
58f5: mov    byte ptr [0x9a1b], 1
58fa: cmp    byte ptr [0x9dde], 1
58ff: je     0x5904
5901: jmp    0x59e7
5904: mov    ax, word ptr [0x420c]
5907: cdq    
5908: sub    word ptr [bp - 0xa], ax
590b: sbb    word ptr [bp - 8], dx
590e: mov    ax, word ptr [0x420e]
5911: jmp    0x59e0
5914: mov    ax, 6
5917: mul    word ptr [bp - 6]
591a: mov    bx, ax
591c: test   byte ptr [bx - 0x6e7a], 0x40
5921: je     0x593e
5923: mov    bl, byte ptr [0x9a1b]
5927: sub    bh, bh
5929: shl    bx, 1
592b: mov    ax, word ptr [bx + 0x420c]
592f: cdq    
5930: add    word ptr [bp - 0xa], ax
5933: adc    word ptr [bp - 8], dx
5936: mov    byte ptr [0x9dde], 1
593b: jmp    0x59e7
593e: mov    bx, word ptr [bp - 0xc]
5941: and    bx, 0x7f
5944: mov    cl, 5
5946: shl    bx, cl
5948: cmp    word ptr [bx + 0x1b0], -1
594d: jne    0x596b
594f: les    bx, ptr [0x86ae]
5953: mov    bx, word ptr es:[bx + 0x3a]
5957: shl    bx, 1
5959: mov    ax, word ptr [bx + 0x424c]
595d: cdq    
595e: add    word ptr [bp - 0xa], ax
5961: adc    word ptr [bp - 8], dx
5964: inc    byte ptr [0x86c2]
5968: jmp    0x5b5e
596b: test   byte ptr [bp - 0xc], 0x80
596f: je     0x597c
5971: les    bx, ptr [0x86ae]
5975: cmp    word ptr es:[bx + 0x3a], 0
597a: je     0x5987
597c: les    bx, ptr [0x86ae]
5980: cmp    word ptr es:[bx + 0x3a], 0
5985: je     0x59ee
5987: mov    bx, word ptr [bp - 0xc]
598a: and    bx, 0x7f
598d: mov    cl, 5
598f: shl    bx, cl
5991: cmp    word ptr [bx + 0x1b0], 0
5996: jne    0x59a8
5998: mov    bx, word ptr [0x86ae]
599c: mov    bx, word ptr es:[bx + 0x3a]
59a0: shl    bx, 1
59a2: mov    ax, word ptr [bx + 0x4246]
59a6: jmp    0x59e0
59a8: mov    bx, word ptr [bp - 0xc]
59ab: and    bx, 0x7f
59ae: mov    cl, 5
59b0: shl    bx, cl
59b2: mov    ax, 0xe
59b5: imul   word ptr [bx + 0x1b0]
59b9: mov    bx, ax
59bb: test   byte ptr [bx + 0x62], 4
59c0: je     0x59d2
59c2: les    bx, ptr [0x86ae]
59c6: mov    bx, word ptr es:[bx + 0x3a]
59ca: shl    bx, 1
59cc: mov    ax, word ptr [bx + 0x423a]
59d0: jmp    0x59e0
59d2: les    bx, ptr [0x86ae]
59d6: mov    bx, word ptr es:[bx + 0x3a]
59da: shl    bx, 1
59dc: mov    ax, word ptr [bx + 0x4240]
59e0: cdq    
59e1: add    word ptr [bp - 0xa], ax
59e4: adc    word ptr [bp - 8], dx
59e7: inc    byte ptr [0x86c1]
59eb: jmp    0x5b5e
59ee: les    bx, ptr [0x86ae]
59f2: mov    bx, word ptr es:[bx + 0x3a]
59f6: shl    bx, 1
59f8: mov    ax, word ptr [bx + 0x4252]
59fc: cdq    
59fd: add    word ptr [bp - 0xa], ax
5a00: adc    word ptr [bp - 8], dx
5a03: inc    byte ptr [0x9986]
5a07: jmp    0x5b5e
5a0a: mov    ax, 6
5a0d: mul    word ptr [bp - 6]
5a10: mov    bx, ax
5a12: test   byte ptr [bx - 0x6e7a], 0x80
5a17: je     0x5a35
5a19: les    bx, ptr [0x86ae]
5a1d: mov    bx, word ptr es:[bx + 0x3a]
5a21: shl    bx, 1
5a23: mov    ax, word ptr [bx + 0x4206]
5a27: cdq    
5a28: add    word ptr [bp - 0xa], ax
5a2b: adc    word ptr [bp - 8], dx
5a2e: mov    byte ptr [0x9a1b], 1
5a33: jmp    0x5a5e
5a35: mov    ax, 6
5a38: mul    word ptr [bp - 6]
5a3b: mov    bx, ax
5a3d: test   byte ptr [bx - 0x6e7a], 0x40
5a42: je     0x5a65
5a44: les    bx, ptr [0x86ae]
5a48: mov    bx, word ptr es:[bx + 0x3a]
5a4c: shl    bx, 1
5a4e: mov    ax, word ptr [bx + 0x420c]
5a52: cdq    
5a53: add    word ptr [bp - 0xa], ax
5a56: adc    word ptr [bp - 8], dx
5a59: mov    byte ptr [0x9dde], 1
5a5e: inc    byte ptr [0x978a]
5a62: jmp    0x5b5e
5a65: mov    bx, word ptr [bp - 0xc]
5a68: and    bx, 0x7f
5a6b: test   byte ptr [bx - 0x6676], 0x40
5a70: je     0x5a8e
5a72: les    bx, ptr [0x86ae]
5a76: mov    bx, word ptr es:[bx + 0x3a]
5a7a: shl    bx, 1
5a7c: mov    ax, word ptr [bx + 0x422e]
5a80: cdq    
5a81: add    word ptr [bp - 0xa], ax
5a84: adc    word ptr [bp - 8], dx
5a87: inc    byte ptr [0x978b]
5a8b: jmp    0x5b5e
5a8e: mov    ax, 6
5a91: mul    word ptr [bp - 6]
5a94: mov    si, ax
5a96: mov    di, word ptr [si - 0x6e7b]
5a9a: and    di, 0xff
5a9e: mov    cl, 4
5aa0: shr    di, cl
5aa2: shl    di, cl
5aa4: mov    bl, byte ptr [si - 0x6e7c]
5aa8: sub    bh, bh
5aaa: shr    bx, cl
5aac: test   byte ptr [bx + di - 0x7486], 3
5ab1: jne    0x5a72
5ab3: mov    bx, word ptr [bp - 0xc]
5ab6: and    bx, 0x7f
5ab9: test   byte ptr [bx - 0x6676], 0x80
5abe: je     0x5adc
5ac0: les    bx, ptr [0x86ae]
5ac4: mov    bx, word ptr es:[bx + 0x3a]
5ac8: shl    bx, 1
5aca: mov    ax, word ptr [bx + 0x4222]
5ace: cdq    
5acf: add    word ptr [bp - 0xa], ax
5ad2: adc    word ptr [bp - 8], dx
5ad5: inc    byte ptr [0x99f5]
5ad9: jmp    0x5b5e
5adc: les    bx, ptr [0x86ae]
5ae0: mov    bx, word ptr es:[bx + 0x3a]
5ae4: mov    cl, 5
5ae6: shl    bx, cl
5ae8: mov    si, word ptr [bp - 0xc]
5aeb: and    si, 0x7f
5aee: mov    al, byte ptr [si - 0x6676]
5af2: cwde   
5af3: mov    si, ax
5af5: and    si, 0xf
5af8: shl    si, 1
5afa: mov    ax, word ptr [bx + si + 0x4258]
5afe: cdq    
5aff: add    word ptr [bp - 0xa], ax
5b02: adc    word ptr [bp - 8], dx
5b05: mov    bx, word ptr [0x86ae]
5b09: mov    bx, word ptr es:[bx + 0x3a]
5b0d: shl    bx, 1
5b0f: cmp    word ptr [bx + 0x421c], 0
5b14: jl     0x5b19
5b16: jmp    0x5a5e
5b19: jmp    0x58ab
5b1c: inc    word ptr [0x8ea4]
5b20: les    bx, ptr [0x86ae]
5b24: mov    bx, word ptr es:[bx + 0x3a]
5b28: shl    bx, 1
5b2a: mov    ax, word ptr [bx + 0x4210]
5b2e: jmp    0x5710
5b31: inc    word ptr [0x9a0c]
5b35: les    bx, ptr [0x86ae]
5b39: mov    bx, word ptr es:[bx + 0x3a]
5b3d: shl    bx, 1
5b3f: mov    ax, word ptr [bx + 0x4234]
5b43: jmp    0x5710
5b46: lodsb  al, byte ptr [si]
5b47: push   di
5b48: or     bl, byte ptr [bp + si - 0x4e]
5b4b: pop    ax
5b4c: pop    si
5b4d: pop    bx
5b4e: pop    si
5b4f: pop    bx
5b50: xor    word ptr [bp + di + 0x1c], bx
5b53: pop    bx
5b54: mov    cl, 0x56
5b56: push   si
5b57: push   di
5b58: rcl    byte ptr [bp + 0x1a]
5b5b: push   di
5b5c: lodsb  al, byte ptr [si]
5b5d: push   di
5b5e: inc    word ptr [bp - 6]
5b61: mov    ax, word ptr [bp + 4]
5b64: cmp    word ptr [bp - 6], ax
5b67: ja     0x5b97
5b69: mov    ax, 6
5b6c: mul    word ptr [bp - 6]
5b6f: mov    si, ax
5b71: cmp    byte ptr [si - 0x6e7a], 0
5b76: je     0x5b97
5b78: mov    al, byte ptr [si - 0x6e79]
5b7c: cwde   
5b7d: mov    word ptr [bp - 0xc], ax
5b80: mov    al, byte ptr [si - 0x6e7a]
5b84: and    ax, 0x3f
5b87: sub    ax, 1
5b8a: cmp    ax, 0xb
5b8d: ja     0x5b5e
5b8f: add    ax, ax
5b91: xchg   bx, ax
5b92: jmp    word ptr cs:[bx + 0x5b46]
5b97: mov    ax, 8
5b9a: cdq    
5b9b: push   dx
5b9c: push   ax
5b9d: push   word ptr [bp - 8]
5ba0: push   word ptr [bp - 0xa]
5ba3: les    bx, ptr [0x86ae]
5ba7: mov    bx, word ptr es:[bx + 0x38]
5bab: shl    bx, 1
5bad: mov    ax, word ptr [bx + 0x42b8]
5bb1: cdq    
5bb2: push   dx
5bb3: push   ax
5bb4: call   0x8d58
5bb7: push   dx
5bb8: push   ax
5bb9: call   0x8cbc
5bbc: mov    word ptr [bp - 0xa], ax
5bbf: mov    word ptr [bp - 8], dx
5bc2: mov    ax, 8
5bc5: cdq    
5bc6: push   dx
5bc7: push   ax
5bc8: push   word ptr [bp - 8]
5bcb: push   word ptr [bp - 0xa]
5bce: les    bx, ptr [0x86ae]
5bd2: mov    bx, word ptr es:[bx + 0x3c]
5bd6: shl    bx, 1
5bd8: mov    ax, word ptr [bx + 0x42c2]
5bdc: cdq    
5bdd: push   dx
5bde: push   ax
5bdf: call   0x8d58
5be2: push   dx
5be3: push   ax
5be4: call   0x8cbc
5be7: mov    word ptr [bp - 0xa], ax
5bea: mov    word ptr [bp - 8], dx
5bed: mov    ax, 8
5bf0: cdq    
5bf1: push   dx
5bf2: push   ax
5bf3: push   word ptr [bp - 8]
5bf6: push   word ptr [bp - 0xa]
5bf9: les    bx, ptr [0x86ae]
5bfd: mov    bx, word ptr es:[bx + 0x3e]
5c01: shl    bx, 1
5c03: mov    ax, word ptr [bx + 0x42ca]
5c07: cdq    
5c08: push   dx
5c09: push   ax
5c0a: call   0x8d58
5c0d: push   dx
5c0e: push   ax
5c0f: call   0x8cbc
5c12: mov    word ptr [bp - 0xa], ax
5c15: mov    word ptr [bp - 8], dx
5c18: mov    ax, 8
5c1b: cdq    
5c1c: push   dx
5c1d: push   ax
5c1e: push   word ptr [bp - 8]
5c21: push   word ptr [bp - 0xa]
5c24: les    bx, ptr [0x86ae]
5c28: mov    bx, word ptr es:[bx + 0x40]
5c2c: shl    bx, 1
5c2e: mov    ax, word ptr [bx + 0x42d2]
5c32: cdq    
5c33: push   dx
5c34: push   ax
5c35: call   0x8d58
5c38: push   dx
5c39: push   ax
5c3a: call   0x8cbc
5c3d: mov    word ptr [bp - 0xa], ax
5c40: mov    word ptr [bp - 8], dx
5c43: cmp    word ptr [bp - 2], 1
5c47: jne    0x5c7b
5c49: les    bx, ptr [0x9ed6]
5c4d: cmp    word ptr es:[bx + 0x26], 2
5c52: jne    0x5c7b
5c54: mov    ax, 8
5c57: cdq    
5c58: push   dx
5c59: push   ax
5c5a: push   word ptr [bp - 8]
5c5d: push   word ptr [bp - 0xa]
5c60: mov    bx, word ptr [0x978c]
5c64: shl    bx, 1
5c66: mov    ax, word ptr [bx + 0x42d8]
5c6a: cdq    
5c6b: push   dx
5c6c: push   ax
5c6d: call   0x8d58
5c70: push   dx
5c71: push   ax
5c72: call   0x8cbc
5c75: mov    word ptr [bp - 0xa], ax
5c78: mov    word ptr [bp - 8], dx
5c7b: les    bx, ptr [0x9ed6]
5c7f: cmp    word ptr es:[bx + 0x2e], 0x1b58
5c85: jb     0x5caf
5c87: cmp    word ptr es:[bx + 0x2e], 0x2328
5c8d: jae    0x5caf
5c8f: mov    ax, 8
5c92: cdq    
5c93: push   dx
5c94: push   ax
5c95: mov    ax, 9
5c98: cdq    
5c99: push   dx
5c9a: push   ax
5c9b: push   word ptr [bp - 8]
5c9e: push   word ptr [bp - 0xa]
5ca1: call   0x8d58
5ca4: push   dx
5ca5: push   ax
5ca6: call   0x8cbc
5ca9: mov    word ptr [bp - 0xa], ax
5cac: mov    word ptr [bp - 8], dx
5caf: les    bx, ptr [0x9ed6]
5cb3: cmp    word ptr es:[bx + 0x2e], 0x2328
5cb9: jb     0x5ce3
5cbb: cmp    word ptr es:[bx + 0x2e], 0x2cec
5cc1: jae    0x5ce3
5cc3: mov    ax, 8
5cc6: cdq    
5cc7: push   dx
5cc8: push   ax
5cc9: mov    ax, 0xa
5ccc: cdq    
5ccd: push   dx
5cce: push   ax
5ccf: push   word ptr [bp - 8]
5cd2: push   word ptr [bp - 0xa]
5cd5: call   0x8d58
5cd8: push   dx
5cd9: push   ax
5cda: call   0x8cbc
5cdd: mov    word ptr [bp - 0xa], ax
5ce0: mov    word ptr [bp - 8], dx
5ce3: les    bx, ptr [0x9ed6]
5ce7: cmp    word ptr es:[bx + 0x2e], 0x2cec
5ced: jb     0x5d0f
5cef: mov    ax, 8
5cf2: cdq    
5cf3: push   dx
5cf4: push   ax
5cf5: mov    ax, 0xb
5cf8: cdq    
5cf9: push   dx
5cfa: push   ax
5cfb: push   word ptr [bp - 8]
5cfe: push   word ptr [bp - 0xa]
5d01: call   0x8d58
5d04: push   dx
5d05: push   ax
5d06: call   0x8cbc
5d09: mov    word ptr [bp - 0xa], ax
5d0c: mov    word ptr [bp - 8], dx
5d0f: mov    ax, word ptr [bp - 0xa]
5d12: mov    dx, word ptr [bp - 8]
5d15: pop    si
5d16: pop    di
5d17: mov    sp, bp
5d19: pop    bp
5d1a: ret    