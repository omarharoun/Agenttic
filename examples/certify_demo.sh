#!/usr/bin/env bash
# Certification demo — end-to-end on the OFFLINE mock provider (no API key).
# Produces an evidence dossier, verifies it offline, shows tamper detection,
# renews it ($0), and revokes it. All deterministic, no network.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

TENANT="certify-demo"
export AGENTTIC_TENANT="$TENANT"
DB="ascore.${TENANT}.db"
OUT="/tmp/agenttic_certify_demo.json"

cleanup() { rm -f "$DB" "$OUT"; }
trap cleanup EXIT

run() { echo; echo "\$ $*"; "$@"; }

echo "=== Agenttic certification demo (offline mock provider) ==="

# 1. Inspect the shipped safety profile — cbrn_proxy renders NOT ASSESSED.
run ascore profiles show cert-agent-safety-v1

# 2. Certify the reference agent → an evidence dossier.
run ascore certify --agent ref-agent --profile cert-agent-safety-v1 --mock -o "$OUT"

DOSSIER_ID="$(python -c "import json,sys;print(json.load(open('$OUT'))['dossier_id'])")"
echo "dossier_id = $DOSSIER_ID"

# 3. Verify the dossier offline (green).
run ascore dossier verify "$OUT"

# 4. Tamper detection — flip a byte, verification must fail naming the ref.
python -c "import json;d=json.load(open('$OUT'));d['agent_id']='EVIL';json.dump(d,open('$OUT','w'))"
echo; echo "\$ ascore dossier verify $OUT   # expect FAILED"
ascore dossier verify "$OUT" || echo "(verification correctly failed)"

# 5. Renew (chained dossier, \$0 for an unchanged agent).
run ascore certify --agent ref-agent --profile cert-agent-safety-v1 --renew --mock

# 6. Revoke (append-only; dossier stays readable, status flips to revoked).
run ascore dossier revoke "$DOSSIER_ID" --reason "demo revocation"

echo; echo "=== demo complete ==="
