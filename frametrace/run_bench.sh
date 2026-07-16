#!/usr/bin/env bash
# 오버헤드 측정을 재현한다. README의 수치는 이 스크립트로 나온 것이다.
set -e

BUILD=${1:-build}

echo "=== 계측 활성 빌드 ==="
"$BUILD/bench_overhead"

echo
echo "=== FT_ENABLED=0 빌드 (계측 제거) ==="
"$BUILD/bench_overhead_disabled"

echo
echo "=== 링 버퍼 테스트 ==="
"$BUILD/test_ring_buffer"
