#!/usr/bin/env bash
# Full scoring run: rsync solution → VM100, build+run docker submission there with
# the official runner (exact 8090 limits), pull predictions back, score DEV/HOLDOUT
# on host, append SCORES/history.jsonl.
# Usage: remote_score.sh <run_id> [input_subdir]   (default input: data/train)
set -euo pipefail

RUN_ID="${1:?usage: remote_score.sh <run_id> [input_subdir]}"
INPUT="${2:-data/train}"
VM=ubuntu@192.168.1.169
RBASE=/mnt/nvme/mib
SOLUTION="$(cd "$(dirname "$0")/.." && pwd)"
CHALLENGE=/home/claude/projects/mib-doc-challenge
OUT="$SOLUTION/SCORES/runs/$RUN_ID"
mkdir -p "$OUT"

rsync -a --delete --exclude .git --exclude SCORES --exclude NOTES \
  "$SOLUTION/" "$VM:$RBASE/solution/"

# Official runner enforces --network none, --cpus 4, --memory 8g, image/model caps.
ssh "$VM" "mkdir -p $RBASE/out/$RUN_ID && cd $RBASE/mib-doc-challenge && \
  /usr/bin/time -f 'WALL_SECONDS %e' python3 scripts/run_docker_submission.py \
    --repo $RBASE/solution \
    --input-dir $INPUT \
    --output $RBASE/out/$RUN_ID/predictions.jsonl \
    --image-tag mib-submission:$RUN_ID 2>&1 | tail -15; \
  docker image inspect mib-submission:$RUN_ID --format 'IMAGE_BYTES {{.Size}}'"

rsync -a "$VM:$RBASE/out/$RUN_ID/predictions.jsonl" "$OUT/predictions.jsonl"
N=$(find "$CHALLENGE/$INPUT" -name '*.pdf' | wc -l)
echo "PDF_COUNT $N"
echo "Now run: python3 $SOLUTION/scripts/score_split.py $RUN_ID $OUT/predictions.jsonl --sec-per-pdf <wall/N> --image-mb <bytes/1e6>"
