# Phase 5: CI/CD

Build the deploy scripts, deploy workflow, and smoke tests. Depends on Phases 1-4 being complete.

---

## P5.1 — Scripts

```
Read CLAUDE.md and docs/DEPLOY.md.

Create scripts/generate_backend.sh:
- Usage: ./generate_backend.sh <bucket_name> <stage_key> <region>
- Generates a backend.tf file in the current directory:
    terraform {
      backend "s3" {
        bucket = "<bucket_name>"
        key    = "<stage_key>/terraform.tfstate"
        region = "<region>"
      }
    }
- Overwrites if exists (idempotent)
- Exit 1 if any argument missing
- chmod +x

Create scripts/generate_tfvars.sh:
- Usage: ./generate_tfvars.sh <ecr_repo_url> <image_tag> <region>
- Generates a terraform.tfvars file in the current directory:
    image_tag  = "<image_tag>"
    ecr_repo   = "<ecr_repo_url>"
    aws_region = "<region>"
- Overwrites if exists (idempotent)
- Exit 1 if any argument missing
- chmod +x

Create scripts/generate_bucket_name.sh:
- Usage: ./generate_bucket_name.sh
- If IAC_CI_STATE_BUCKET is set, echo it
- Otherwise generate: "iac-ci-state-$(head -c 3 /dev/urandom | xxd -p | cut -c1-5)"
- Echo the bucket name to stdout (for capture in workflow)
- chmod +x
```

## P5.2 — Deploy Workflow

```
Read CLAUDE.md and docs/DEPLOY.md (full document).

Create .github/workflows/deploy.yml:

name: Deploy
on:
  workflow_dispatch:
    inputs:
      aws_region:
        description: 'AWS Region'
        required: true
        default: 'us-east-1'
      state_bucket_name:
        description: 'S3 bucket for TF state (auto-generated if empty)'
        required: false
        default: ''

env:
  TF_VAR_aws_region: ${{ inputs.aws_region }}

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: ${{ inputs.aws_region }}

      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: '1.5'

      - uses: actions/setup-python@v5
        with:
          python-version: '3.14'

      # Step 1: Bootstrap state bucket
      - name: Resolve bucket name
        id: bucket
        run: |
          if [ -n "${{ inputs.state_bucket_name }}" ]; then
            echo "name=${{ inputs.state_bucket_name }}" >> $GITHUB_OUTPUT
          else
            echo "name=$(bash scripts/generate_bucket_name.sh)" >> $GITHUB_OUTPUT
          fi

      - name: Bootstrap state bucket
        run: |
          cd infra/00-bootstrap
          terraform init
          terraform apply -auto-approve \
            -var "state_bucket_name=${{ steps.bucket.outputs.name }}" \
            -var "aws_region=${{ inputs.aws_region }}"

      # Step 2: Create ECR
      - name: Deploy ECR
        run: |
          cd infra/01-ecr
          bash ../../scripts/generate_backend.sh \
            "${{ steps.bucket.outputs.name }}" "ecr" "${{ inputs.aws_region }}"
          terraform init
          terraform apply -auto-approve \
            -var "aws_region=${{ inputs.aws_region }}"

      - name: Get ECR repo URL
        id: ecr
        run: |
          cd infra/01-ecr
          echo "url=$(terraform output -raw ecr_repo_url)" >> $GITHUB_OUTPUT

      # Step 3: Build and push Docker image
      - name: Login to ECR
        run: |
          aws ecr get-login-password --region ${{ inputs.aws_region }} | \
            docker login --username AWS --password-stdin ${{ steps.ecr.outputs.url }}

      - name: Build and push
        run: |
          docker build -f docker/Dockerfile -t ${{ steps.ecr.outputs.url }}:${{ github.sha }} -t ${{ steps.ecr.outputs.url }}:latest .
          docker push ${{ steps.ecr.outputs.url }}:${{ github.sha }}
          docker push ${{ steps.ecr.outputs.url }}:latest

      # Step 4: Deploy system
      - name: Deploy infrastructure
        run: |
          cd infra/02-deploy
          bash ../../scripts/generate_backend.sh \
            "${{ steps.bucket.outputs.name }}" "deploy" "${{ inputs.aws_region }}"
          bash ../../scripts/generate_tfvars.sh \
            "${{ steps.ecr.outputs.url }}" "${{ github.sha }}" "${{ inputs.aws_region }}"
          terraform init
          terraform apply -auto-approve

      - name: Get deploy outputs
        id: deploy
        run: |
          cd infra/02-deploy
          echo "api_url=$(terraform output -raw api_gateway_url)" >> $GITHUB_OUTPUT

      # Step 5: Smoke tests
      - name: Run smoke tests
        run: |
          cd infra/02-deploy
          export API_GATEWAY_URL=$(terraform output -raw api_gateway_url)
          export AWS_REGION=${{ inputs.aws_region }}
          export ORDERS_TABLE=$(terraform output -raw orders_table_name)
          export ORDER_EVENTS_TABLE=$(terraform output -raw order_events_table_name)
          export LOCKS_TABLE=$(terraform output -raw locks_table_name)
          export INTERNAL_BUCKET=$(terraform output -raw internal_bucket_name)
          export DONE_BUCKET=$(terraform output -raw done_bucket_name)
          bash ../../tests/smoke/test_deploy.sh

      # Step 6: Archive for teardown
      - name: Create teardown archive
        run: |
          mkdir -p /tmp/teardown/infra/01-ecr
          mkdir -p /tmp/teardown/infra/02-deploy

          cp infra/01-ecr/*.tf /tmp/teardown/infra/01-ecr/
          cp infra/01-ecr/backend.tf /tmp/teardown/infra/01-ecr/ 2>/dev/null || true

          cp infra/02-deploy/*.tf /tmp/teardown/infra/02-deploy/
          cp infra/02-deploy/backend.tf /tmp/teardown/infra/02-deploy/ 2>/dev/null || true
          cp infra/02-deploy/terraform.tfvars /tmp/teardown/infra/02-deploy/ 2>/dev/null || true

          cat > /tmp/teardown/TEARDOWN.md << 'TDEOF'
          # Teardown Instructions
          
          1. cd infra/02-deploy && terraform init && terraform destroy -auto-approve
          2. cd infra/01-ecr && terraform init && terraform destroy -auto-approve
          3. Manually delete state bucket: aws s3 rb s3://${{ steps.bucket.outputs.name }} --force
          TDEOF

          cd /tmp/teardown
          zip -r /tmp/iac-ci-teardown-${{ github.sha }}.zip .

      - name: Upload teardown archive
        run: |
          aws s3 cp /tmp/iac-ci-teardown-${{ github.sha }}.zip \
            s3://${{ steps.bucket.outputs.name }}/archive/iac-ci-teardown-${{ github.sha }}.zip

      - name: Summary
        run: |
          echo "## Deploy Complete" >> $GITHUB_STEP_SUMMARY
          echo "- API Gateway: ${{ steps.deploy.outputs.api_url }}" >> $GITHUB_STEP_SUMMARY
          echo "- State Bucket: ${{ steps.bucket.outputs.name }}" >> $GITHUB_STEP_SUMMARY
          echo "- Image Tag: ${{ github.sha }}" >> $GITHUB_STEP_SUMMARY
          echo "- Teardown archive: s3://${{ steps.bucket.outputs.name }}/archive/iac-ci-teardown-${{ github.sha }}.zip" >> $GITHUB_STEP_SUMMARY
```

## P5.3 — Smoke Tests

```
Read CLAUDE.md and docs/DEPLOY.md (Step 5 section).

Create tests/smoke/test_deploy.sh:

Expects environment variables:
- API_GATEWAY_URL
- AWS_REGION
- ORDERS_TABLE
- ORDER_EVENTS_TABLE
- LOCKS_TABLE
- INTERNAL_BUCKET
- DONE_BUCKET

Tests (each prints PASS/FAIL):
1. API Gateway responds: curl -s -o /dev/null -w "%{http_code}" -X POST ${API_GATEWAY_URL}/init → expect 4xx (no payload is fine, just not 5xx or connection refused)
2. init_job Lambda exists: aws lambda get-function --function-name iac-ci-init-job
3. orchestrator Lambda exists: aws lambda get-function --function-name iac-ci-orchestrator
4. watchdog_check Lambda exists: aws lambda get-function --function-name iac-ci-watchdog-check
5. worker Lambda exists: aws lambda get-function --function-name iac-ci-worker
6. Orders DynamoDB table exists: aws dynamodb describe-table --table-name ${ORDERS_TABLE}
7. Order events DynamoDB table exists: aws dynamodb describe-table --table-name ${ORDER_EVENTS_TABLE}
8. Locks DynamoDB table exists: aws dynamodb describe-table --table-name ${LOCKS_TABLE}
9. Internal S3 bucket exists: aws s3api head-bucket --bucket ${INTERNAL_BUCKET}
10. Done S3 bucket exists: aws s3api head-bucket --bucket ${DONE_BUCKET}
11. Step Function exists: aws stepfunctions list-state-machines (grep for iac-ci-watchdog)
12. S3 notification configured: aws s3api get-bucket-notification-configuration --bucket ${INTERNAL_BUCKET} (verify LambdaFunctionConfigurations present)

Exit 0 if all pass, exit 1 on any failure.
chmod +x.
```

## P5.4 — Update GitHub Actions Test Workflow (Phase 5)

```
Update .github/workflows/test.yml to add Phase 5 script tests.

Add a new job that depends on phase4-terraform:

  phase5-scripts:
    needs: phase4-terraform
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Test generate_backend.sh
        run: |
          bash scripts/generate_backend.sh test-bucket ecr us-east-1
          cat backend.tf
          grep -q 'bucket = "test-bucket"' backend.tf
          grep -q 'key    = "ecr/terraform.tfstate"' backend.tf
          rm backend.tf
      - name: Test generate_tfvars.sh
        run: |
          bash scripts/generate_tfvars.sh 123456.dkr.ecr.us-east-1.amazonaws.com/iac-ci abc123 us-east-1
          cat terraform.tfvars
          grep -q 'image_tag' terraform.tfvars
          grep -q 'ecr_repo' terraform.tfvars
          rm terraform.tfvars
      - name: Test generate_bucket_name.sh
        run: |
          BUCKET=$(bash scripts/generate_bucket_name.sh)
          echo "Generated: $BUCKET"
          [[ "$BUCKET" == iac-ci-state-* ]]
      - name: Test generate_bucket_name.sh with env override
        run: |
          IAC_CI_STATE_BUCKET=my-custom-bucket BUCKET=$(bash scripts/generate_bucket_name.sh)
          [ "$BUCKET" = "my-custom-bucket" ]
      - name: Validate deploy workflow syntax
        run: |
          pip install pyyaml
          python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))"
      - name: Validate test workflow syntax
        run: |
          python -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))"
```
