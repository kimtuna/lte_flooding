#!/usr/bin/env bash
PID="$1"
OUT="${2:-enb_resource.log}"

if [ -z "$PID" ]; then
  echo "사용법: $0 <srsenb PID> [로그파일]" >&2
  exit 1
fi

echo "# time_s cpu% mem%" > "$OUT"

while kill -0 "$PID" 2>/dev/null; do
  ts=$(date +%s.%N)
  # ps로 CPU/MEM 뽑기
  read cpu mem < <(ps -p "$PID" -o %cpu=,%mem=)
  echo "$ts $cpu $mem" >> "$OUT"
  sleep 0.5
done