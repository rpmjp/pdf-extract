#!/bin/bash
API=localhost:8003
PDF=${1:-statement.pdf}

extract() { grep -oE "\"$1\":\"[^\"]+\"" | head -1 | cut -d'"' -f4; }
extract_id() { grep -oE '"id":[0-9]+' | head -1 | grep -oE '[0-9]+'; }

upload_or_get() {
  local resp=$(curl -s -F "file=@$1" $API/documents)
  local id=$(echo "$resp" | extract_id)
  if [[ -z "$id" ]]; then
    id=$(echo "$resp" | grep -oE 'id=[0-9]+' | grep -oE '[0-9]+')
  fi
  echo "$id"
}

wait_for_job() {
  local job=$1
  for i in {1..90}; do
    local s=$(curl -s $API/jobs/$job | extract status)
    echo "  [$i] $s" >&2
    [[ "$s" == "success" || "$s" == "failed" ]] && { echo "$s"; return; }
    sleep 2
  done
  echo "timeout"
}

echo "=== Test 1: Happy path ==="
ID=$(upload_or_get "$PDF")
echo "Doc id: $ID"
JOB=$(curl -s -X POST $API/documents/$ID/parse | extract job_id)
echo "Job id: $JOB"
RESULT=$(wait_for_job "$JOB")
[[ "$RESULT" == "success" ]] && echo "PASS: happy path" || echo "FAIL: happy path ($RESULT)"

echo ""
echo "=== Test 2: Idempotency ==="
J1=$(curl -s -X POST $API/documents/$ID/parse | extract job_id)
J2=$(curl -s -X POST $API/documents/$ID/parse | extract job_id)
J3=$(curl -s -X POST $API/documents/$ID/parse | extract job_id)
echo "Jobs: $J1 / $J2 / $J3"
[[ "$J1" == "$J2" && "$J2" == "$J3" && -n "$J1" ]] && echo "PASS: idempotency" || echo "FAIL: idempotency"
wait_for_job "$J1" > /dev/null

echo ""
echo "=== Test 3: Crash recovery ==="
JOB=$(curl -s -X POST $API/documents/$ID/parse | extract job_id)
echo "Job: $JOB"
sleep 3
echo "Restarting worker..."
docker compose restart worker
RESULT=$(wait_for_job "$JOB")
[[ "$RESULT" == "success" ]] && echo "PASS: crash recovery" || echo "FAIL: crash recovery ($RESULT)"

echo ""
echo "=== Test 4: Serialization ==="
PDF2=${2:-statement_prose.pdf}
PDF3=${3:-statement_multipage.pdf}
PDF4=${4:-statement_noisy.pdf}
ID2=$(upload_or_get "$PDF2")
ID3=$(upload_or_get "$PDF3")
ID4=$(upload_or_get "$PDF4")

JA=$(curl -s -X POST $API/documents/$ID2/parse | extract job_id)
JB=$(curl -s -X POST $API/documents/$ID3/parse | extract job_id)
JC=$(curl -s -X POST $API/documents/$ID4/parse | extract job_id)
echo "Queued: $JA / $JB / $JC"

MAX=0
for i in {1..120}; do
  A=$(curl -s $API/jobs/$JA | extract status)
  B=$(curl -s $API/jobs/$JB | extract status)
  C=$(curl -s $API/jobs/$JC | extract status)
  RUNNING=0
  [[ "$A" == "started" ]] && RUNNING=$((RUNNING+1))
  [[ "$B" == "started" ]] && RUNNING=$((RUNNING+1))
  [[ "$C" == "started" ]] && RUNNING=$((RUNNING+1))
  [[ $RUNNING -gt $MAX ]] && MAX=$RUNNING
  echo "  [$i] A=$A B=$B C=$C  concurrent=$RUNNING"
  terminal=true
  for s in "$A" "$B" "$C"; do
    [[ "$s" == "queued" || "$s" == "started" || "$s" == "retrying" ]] && terminal=false
  done
  $terminal && break
  sleep 2
done
[[ $MAX -le 1 ]] && echo "PASS: serialization (max concurrent = $MAX)" || echo "FAIL: $MAX ran concurrently"

echo ""
echo "=== Done ==="
