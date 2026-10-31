#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

run() { echo "==> $*"; "$@" > /dev/null; }

echo "==> tests"; python3 -m pytest -q
run python3 eval_support.py
run python3 eval_support.py --limit 0 --stem support_limit0
run python3 eval_support.py --limit 1000 --stem support_limit1000
run python3 eval_support.py --operator deny_all --stem support_deny
run python3 eval_support.py --operator deny_all --no-reopen --stem support_deny_v1
run python3 eval_support.py --gen-seed 9090 --stem support_dev
run python3 eval_support.py --gen-seed 9090 --operator deny_all --stem support_dev_deny
run python3 scale_test.py
echo "==> regression check"; python3 check.py
python3 -c "from majhar.report import fingerprint; print('code fingerprint:', fingerprint())"
