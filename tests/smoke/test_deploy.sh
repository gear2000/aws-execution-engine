#!/usr/bin/env bash
set -euo pipefail

PASSED=0
FAILED=0

pass() { echo "PASS: $1"; ((PASSED++)); }
fail() { echo "FAIL: $1"; ((FAILED++)); }

# Validate required env vars
for var in API_GATEWAY_URL AWS_REGION ORDERS_TABLE ORDER_EVENTS_TABLE LOCKS_TABLE INTERNAL_BUCKET DONE_BUCKET; do
  if [ -z "${!var:-}" ]; then
    echo "ERROR: $var is not set" >&2
    exit 1
  fi
done

# 1. API Gateway responds (expect 4xx, not 5xx or connection error)
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${API_GATEWAY_URL}/init" 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" =~ ^4[0-9]{2}$ ]]; then
  pass "API Gateway responds (HTTP $HTTP_CODE)"
else
  fail "API Gateway responds (HTTP $HTTP_CODE, expected 4xx)"
fi

# 2-5. Lambda functions exist
for FUNC in aws-exe-sys-init-job aws-exe-sys-orchestrator aws-exe-sys-watchdog-check aws-exe-sys-worker; do
  if aws lambda get-function --function-name "$FUNC" --region "$AWS_REGION" >/dev/null 2>&1; then
    pass "Lambda $FUNC exists"
  else
    fail "Lambda $FUNC not found"
  fi
done

# 6-8. DynamoDB tables exist
for TABLE_VAR in ORDERS_TABLE ORDER_EVENTS_TABLE LOCKS_TABLE; do
  TABLE="${!TABLE_VAR}"
  if aws dynamodb describe-table --table-name "$TABLE" --region "$AWS_REGION" >/dev/null 2>&1; then
    pass "DynamoDB table $TABLE exists"
  else
    fail "DynamoDB table $TABLE not found"
  fi
done

# 9-10. S3 buckets exist
for BUCKET_VAR in INTERNAL_BUCKET DONE_BUCKET; do
  BUCKET="${!BUCKET_VAR}"
  if aws s3api head-bucket --bucket "$BUCKET" --region "$AWS_REGION" 2>/dev/null; then
    pass "S3 bucket $BUCKET exists"
  else
    fail "S3 bucket $BUCKET not found"
  fi
done

# 11. Step Function exists
if aws stepfunctions list-state-machines --region "$AWS_REGION" 2>/dev/null | grep -q "aws-exe-sys-watchdog"; then
  pass "Step Function aws-exe-sys-watchdog exists"
else
  fail "Step Function aws-exe-sys-watchdog not found"
fi

# 12. S3 notification configured on internal bucket
NOTIF=$(aws s3api get-bucket-notification-configuration --bucket "$INTERNAL_BUCKET" --region "$AWS_REGION" 2>/dev/null || echo "{}")
if echo "$NOTIF" | grep -q "LambdaFunctionConfigurations"; then
  pass "S3 notification configured on $INTERNAL_BUCKET"
else
  fail "S3 notification not configured on $INTERNAL_BUCKET"
fi

echo ""
echo "Results: $PASSED passed, $FAILED failed"

if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
