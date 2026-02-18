# Architecture

## System Overview

iac-ci is a generic, event-driven execution system. It receives jobs containing orders, queues them, and executes them via AWS Lambda, CodeBuild, or SSM Run Command with dependency resolution, cross-account credential management, and VCS PR tracking.

The system is split into three flows:

- **Part 1 (init_job):** Receive, validate, package (with SOPS), and queue Lambda/CodeBuild orders
- **Part 1b (ssm_config):** Receive, validate, package (no SOPS), and queue SSM Run Command orders
- **Part 2 (execute_orders):** Orchestrate execution of queued orders across all three targets

---

## Part 1: init_job (ProcessWebhook Lambda)

### Upstream

Commands are resolved before entering this system. For example, an IAC layer translates `tf_plan:::tofu:1.6` into concrete shell commands before triggering. By the time a job reaches init_job, every order has a concrete `cmds[]` list. This system does not know or care what the commands do.

### Trigger Sources

```
┌─────────────────────────────────────────────────────────────┐
│                     TRIGGER SOURCES                         │
│   SNS  │  Direct Invoke  │  Step Function  │  Lambda URL    │
└────────────────────────┬────────────────────────────────────┘
                         │ cmds[] already resolved
                         ▼
                  API Gateway (default)
                  POST /webhook   → init_job Lambda
                  POST /ssm       → ssm_config Lambda
                  GitHub webhook signature verification
```

### Flow

```
process_job_and_insert_orders()

inputs:
  - job_parameters_b64
  - trace_id (auto-gen if missing)
  - run_id (auto-gen if missing)
  - done_endpt (auto-gen if missing)

trace format: <trace_id>:<epoch_time>
new leg = same trace_id, new epoch
```

**Step 1: Generate Flow ID + Validate**

```
flow_id = <username>:<trace_id>-<flow_label>

Validate each order:
  - cmds[] exists and non-empty
  - s3 location OR token + repo + folder
  - timeout present

Any failure → error out (fail fast)
```

**Step 2: Repackage Each Order**

```
For each order:
  - get code (s3 or git)
  - fetch SSM/Secrets Manager values
  - get target account temp credentials
  - generate presigned S3 PUT URL for callback
  - merge all with env_vars
  - encrypt everything with SOPS (auto-gen age key if none provided)
  - store SOPS private key in SSM Parameter Store as SecureString
    path: /iac-ci/sops-keys/<run_id>/<order_num>
  - write env_vars.env
  - write secrets.src
  - re-zip tarball
```

**Step 3: Upload to S3**

```
s3://<internal-bucket>/tmp/exec/<run_id>/<order_num>/exec.zip

Optional: stripped copy (no SOPS secrets) if copy_secrets requested
```

**Step 4: Insert Orders to DynamoDB**

```
Orders table — Key: <run_id>:<order_num>

Fields:
  trace_id, flow_id, order_name, cmds, queue_id,
  s3_location, callback_url, execution_target,
  git (repo, token_loc, ssh_key_loc, folder) as b64,
  dependencies, status ("queued"), created_at, last_update,
  timeout, must_succeed (default: true),
  execution_url, step_function_url
```

**Step 5: Init PR Comment**

```
Uses VcsPrHelper to post initial comment on PR.

Comment body:
  #search_tag
  #run_id: <run_id>
  #flow_id: <flow_id>

  Order Summary:
  ├─ order-1: queued
  ├─ order-2: queued
  └─ order-3: queued
```

**Step 6: Kick Off Orchestrator**

```
Write init trigger:
  s3://<internal-bucket>/tmp/callbacks/runs/<run_id>/0000/result.json

S3 event fires → orchestrator Lambda invoked
```

**Response**

```
HTTP 200/400
run_id
trace_id
flow_id
done_endpt
pr_search_tag
init_pr_comment
```

---

## Part 1b: ssm_config (SSM Config Provider Lambda)

Separate construction point for SSM Run Command orders. Receives jobs via `POST /ssm`, uses `SsmJob`/`SsmOrder` models instead of `Job`/`Order`, and does not use SOPS encryption.

### Flow

```
process_ssm_job()

inputs:
  - job_parameters_b64 (SsmJob payload)
  - trace_id (auto-gen if missing)
  - run_id (auto-gen if missing)
  - done_endpt (auto-gen if missing)
```

**Step 1: Validate SSM Orders**

```
Validate each order:
  - cmds[] exists and non-empty
  - timeout present and positive
  - ssm_targets is present
  - ssm_targets contains 'instance_ids' or 'tags'

Any failure → error out (fail fast)
```

**Step 2: Repackage (No SOPS)**

```
For each order:
  - get code (s3 or git) — same code_source logic as init_job
  - fetch SSM/Secrets Manager values (plain, not SOPS-encrypted)
  - generate presigned S3 PUT URL for callback
  - merge all with env_vars into env_dict
  - write cmds.json + env_vars.json into code dir
  - zip into exec.zip

No SOPS encryption — credentials passed via SSM command parameters.
```

**Step 3: Upload to S3**

```
s3://<internal-bucket>/tmp/exec/<run_id>/<order_num>/exec.zip
(reuses init_job upload logic)
```

**Step 4: Insert Orders to DynamoDB**

```
Orders table — Key: <run_id>:<order_num>

Fields:
  trace_id, flow_id, order_name, cmds, queue_id,
  s3_location, callback_url, execution_target ("ssm"),
  ssm_targets, ssm_document_name (optional),
  env_dict (plain, not SOPS),
  dependencies, status ("queued"), created_at, last_update,
  timeout, must_succeed (default: true)
```

**Step 5: Kick Off Orchestrator**

```
Write init trigger:
  s3://<internal-bucket>/tmp/callbacks/runs/<run_id>/0000/result.json

S3 event fires → orchestrator Lambda invoked
(same mechanism as init_job)
```

**Response**

```
HTTP 200/400
run_id
trace_id
flow_id
done_endpt
```

**Key differences from init_job:**
- No SOPS encryption — env vars and credentials are passed as plain SSM command parameters
- No PR comment step — SSM orders are typically infrastructure-level, not PR-driven
- Uses `SsmJob`/`SsmOrder` models with SSM-specific fields (`ssm_targets`, `ssm_document_name`)
- Orders always get `execution_target: "ssm"` in DynamoDB

---

## Part 2: execute_orders (Orchestrator Lambda)

### Trigger Mechanism

```
S3 event notification:
  prefix: tmp/callbacks/runs/
  suffix: result.json
  event:  s3:ObjectCreated:*
  → orchestrator Lambda

All triggers use the same path pattern:
  Init:     tmp/callbacks/runs/<run_id>/0000/result.json
  Workers:  tmp/callbacks/runs/<run_id>/<order_num>/result.json

Orchestrator extracts run_id from path.
```

### Flow

**Lock Acquisition**

```
DynamoDB conditional put: lock:<run_id>
  - Acquired → continue
  - Exists   → EXIT (already handling this run_id)
```

**Step 1: Read State**

```
Query DynamoDB orders table: all orders for <run_id>:*

Check S3 for completed order callbacks:
  tmp/callbacks/runs/<run_id>/<order_num>/result.json

For each new result.json:
  - parse {"status", "log"}
  - update orders table status
  - write to order_events table
  - update PR comment via VcsPrHelper
```

**Step 2: Evaluate Dependencies**

```
For each "queued" order:
  - No deps              → ready
  - All deps succeeded   → ready
  - Any dep failed + must_succeed → fail this order
  - Deps still running   → skip
```

**Step 3: Dispatch (parallel)**

```
For each ready order:
  - execution_target="lambda"    → invoke worker Lambda
  - execution_target="codebuild" → start CodeBuild project
  - execution_target="ssm"       → send SSM Run Command

  Backward compat: if use_lambda is present but execution_target is not,
    use_lambda=true  → lambda
    use_lambda=false → codebuild

  - Start watchdog Step Function for this order
  - Update orders table: status → running
  - Write order_event: dispatched
  - Update PR comment
```

**Step 4: Check Completion**

```
All orders done (succeeded/failed/timed_out)?
  NO  → release lock (next S3 callback will re-trigger)
  YES → finalize
```

**Step 5: Finalize**

```
- Write job-level order_event (_job:epoch) with:
    status: succeeded/failed/timed_out
    done_endpt reference
    summary: {succeeded: N, failed: N, timed_out: N}
- Write done endpoint:
    s3://<done-bucket>/<run_id>/done
- Final PR comment update (full summary)
- Release lock: status → "completed"
```

### Job-Level Status Resolution

```
- All orders succeeded                    → job succeeded
- Any must_succeed order failed           → job failed
- Job-level timeout exceeded              → job timed_out
```

---

## Worker

Same code runs in Lambda and CodeBuild Docker container.

```
1. Fetch exec.zip from S3
2. Fetch SOPS private key from SSM Parameter Store
   path: /iac-ci/sops-keys/<run_id>/<order_num>
3. Decrypt SOPS → env vars:
   - AWS creds (target account)
   - custom env vars
   - CALLBACK_URL (presigned S3 PUT)
4. Run cmds (os.system / subprocess.Popen, no buffering)
5. Capture exit code + stdout/stderr
6. PUT to CALLBACK_URL:
   {"status": "succeeded/failed/timed_out", "log": "<stdout+stderr>"}
7. S3 event fires → orchestrator re-triggered
```

No IAM role switching. Worker only operates with target account credentials from SOPS. Worker needs ssm:GetParameter on /iac-ci/sops-keys/* to fetch the decryption key. Callback uses presigned URL (no additional AWS permissions needed).

---

## SSM Run Command Execution

For orders with `execution_target: "ssm"`, the orchestrator sends an SSM Run Command instead of invoking Lambda or CodeBuild.

```
1. Orchestrator calls ssm:SendCommand with:
   - DocumentName: order-level ssm_document_name OR default "iac-ci-run-commands"
   - Targets: instance_ids or tag-based targeting from ssm_targets
   - Parameters:
       Commands:    JSON array of shell commands
       CallbackUrl: presigned S3 PUT URL
       Timeout:     order timeout in seconds
       EnvVars:     JSON object of env vars (credentials included, plain text)
       S3Location:  S3 URI for exec.zip (optional)

2. SSM Document (iac-ci-run-commands) on target instance:
   - Export env vars from EnvVars parameter
   - Download and extract exec.zip from S3 (if provided)
   - Execute commands sequentially
   - Capture exit code + stdout/stderr
   - PUT result.json to CallbackUrl (presigned URL)

3. S3 event fires → orchestrator re-triggered
```

No SOPS. Credentials and env vars are passed directly as SSM command parameters. The SSM document handles env var export, command execution, and callback — same result.json contract as Lambda/CodeBuild workers.

---

## Watchdog (Timeout Safety Net)

Per-order Step Function started at dispatch time.

```
┌─────────────────────────┐
│ Check: result.json      │
│ exists in S3?           │←──┐
│                         │   │
│ YES → EXIT              │   │
│                         │   │
│ NO  → timeout exceeded? │   │
│   YES → write           │   │
│     result.json         │   │
│     {status: timed_out} │   │
│     → EXIT              │   │
│   NO  → Wait 60s ───────┘   │
└─────────────────────────────┘
```

Timeout distributed to three places: SOPS env var (worker self-enforces), DynamoDB (record keeping), Step Function (safety net).

---

## Cross-Account Execution Model

```
Repackage step (init_job):
  SSM/Secrets Manager → target account temp creds
  + env_vars + presigned callback URL
  → all encrypted via SOPS (age key) → packed into exec.zip
  SOPS private key → SSM Parameter Store (SecureString)
    /iac-ci/sops-keys/<run_id>/<order_num>

Worker:
  Fetch SOPS private key from SSM Parameter Store
  Decrypt SOPS → env vars include:
    AWS_ACCESS_KEY_ID     (target account)
    AWS_SECRET_ACCESS_KEY (target account)
    AWS_SESSION_TOKEN     (target account)
    CALLBACK_URL          (presigned, no creds needed)
    CUSTOM_VAR_1, etc.

  Run cmds → operates as target account
  Callback → presigned URL, zero AWS context needed
```

No dual-credential context switching. Worker has a single execution context.

---

## S3 Layout

### Internal Bucket (1 day lifecycle)

```
s3://<internal-bucket>/
└── tmp/
    ├── exec/
    │   └── <run_id>/
    │       └── <order_num>/
    │           └── exec.zip
    │
    └── callbacks/runs/
        └── <run_id>/
            ├── 0000/
            │   └── result.json        (init trigger)
            └── <order_num>/
                └── result.json        (worker callback)
```

### Done Bucket (1 day lifecycle, separate for security)

```
s3://<done-bucket>/
└── <run_id>/
    └── done
```

### State Bucket (permanent)

```
s3://<state-bucket>/
├── ecr/terraform.tfstate
├── deploy/terraform.tfstate
└── archive/iac-ci-teardown-<sha>.zip
```

---

## DynamoDB Tables

### Orders

```
PK: <run_id>:<order_num>
TTL: 1 day

Fields: trace_id, flow_id, order_name, cmds, queue_id,
s3_location, callback_url, execution_target, git (b64),
dependencies, status, created_at, last_update, timeout,
must_succeed, execution_url, step_function_url,
ssm_targets (SSM only), ssm_document_name (SSM only),
env_dict (SSM only, plain credentials)
```

### Order Events

```
PK: trace_id
SK: order_name:epoch
TTL: 90 days

GSI PK: order_name
GSI SK: epoch

Order-level events:
  trace_id | deploy-vpc:1708099200 | dispatched
  trace_id | deploy-vpc:1708099240 | succeeded

Job-level events (reserved name "_job"):
  trace_id | _job:1708099100 | job_started
  trace_id | _job:1708099300 | job_completed
```

### Orchestrator Locks

```
PK: lock:<run_id>
TTL: max_timeout

Fields: orchestrator_id, status (active/completed),
acquired_at, flow_id, trace_id
```

---

## Tracing

```
trace_id format: <trace_id>:<epoch_time>
  - Same trace_id across entire run
  - New epoch per significant leg

flow_id format: <username>:<trace_id>-<flow_label>
  - flow_label provided by caller or defaults to "exec"
  - Stored in DynamoDB, PR comments, passed to scheduler

Example trace across legs:
  a3f7b2c1:1708099200  (validation)
  a3f7b2c1:1708099203  (repackage)
  a3f7b2c1:1708099207  (upload)
  a3f7b2c1:1708099208  (insert)
  a3f7b2c1:1708099209  (pr_comment)
  a3f7b2c1:1708099210  (dispatch)
  a3f7b2c1:1708099240  (orch_read)
  a3f7b2c1:1708099241  (orch_eval)
  a3f7b2c1:1708099242  (orch_dispatch)
  a3f7b2c1:1708099300  (finalize)
```

---

## TTL / Cleanup Strategy

```
Presigned URLs:        default 2 hours (configurable in job_params)
Internal S3 bucket:    1 day lifecycle rule
Done S3 bucket:        1 day lifecycle rule
Orders table:          1 day DynamoDB TTL
Orchestrator locks:    TTL = max_timeout of run
Order events table:    90 day DynamoDB TTL (+ GSI for analytics)
SOPS keys (SSM):       deleted on job finalization by orchestrator
```

Nothing runs 24/7. No manual cleanup required.

---

## Event-Driven Wave Execution Example

```
Orders:
  order-1: no deps
  order-2: no deps
  order-3: depends on [order-1, order-2]

Timeline:

init_job writes .../0000/result.json
  → S3 event → orchestrator
  → dispatches order-1, order-2 (parallel)
  → releases lock

order-1 finishes, writes result.json
  → S3 event → orchestrator
  → acquires lock
  → order-3 deps: order-1 ✅, order-2 still running
  → nothing to dispatch
  → releases lock

order-2 finishes, writes result.json
  → S3 event → orchestrator
  → acquires lock
  → order-3 deps: order-1 ✅, order-2 ✅
  → dispatches order-3
  → releases lock

order-3 finishes, writes result.json
  → S3 event → orchestrator
  → acquires lock
  → all done → finalize
  → write done endpoint
  → release lock
```
