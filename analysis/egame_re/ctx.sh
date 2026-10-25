#!/bin/sh
# ctx.sh ADDR BEFORE AFTER : lines around an address in "$(dirname "$0")"/egame_all.asm
n=$(grep -n "^0*$1 " "$(dirname "$0")"/egame_all.asm | head -1 | cut -d: -f1)
sed -n "$((n-$2)),$((n+$3))p" "$(dirname "$0")"/egame_all.asm
