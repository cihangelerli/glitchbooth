#!/bin/bash

CAP_DIR="/home/glitchbooth/glitch-booth/captures"
LIMIT=500

cd "$CAP_DIR" || exit 0

# get sorted list of bw files (each represents 1 capture event)
mapfile -t files < <(ls *_bw.jpg 2>/dev/null | sort)

count=${#files[@]}

# under limit → exit immediately (zero CPU impact)
if [ "$count" -le "$LIMIT" ]; then
    exit 0
fi

delete_count=$((count - LIMIT))

for ((i=0; i<delete_count; i++)); do
    base="${files[$i]%%_bw.jpg}"

    # safe paired deletion
    rm -f "${base}_bw.jpg"
    rm -f "${base}_color.jpg"
done

