#!/usr/bin/env bash
set -euo pipefail

if [ -n "${IAC_CI_STATE_BUCKET:-}" ]; then
  echo "$IAC_CI_STATE_BUCKET"
else
  echo "iac-ci-state-$(head -c 3 /dev/urandom | xxd -p | cut -c1-5)"
fi
