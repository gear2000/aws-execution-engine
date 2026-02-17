locals {
  lambda_env = {
    IAC_CI_ORDERS_TABLE       = aws_dynamodb_table.orders.name
    IAC_CI_ORDER_EVENTS_TABLE = aws_dynamodb_table.order_events.name
    IAC_CI_LOCKS_TABLE        = aws_dynamodb_table.orchestrator_locks.name
    IAC_CI_INTERNAL_BUCKET    = aws_s3_bucket.internal.id
    IAC_CI_DONE_BUCKET        = aws_s3_bucket.done.id
  }
}

# --- init_job ---

resource "aws_lambda_function" "init_job" {
  function_name = "iac-ci-init-job"
  role          = aws_iam_role.init_job.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  timeout       = 300
  memory_size   = 512

  image_config {
    command = ["src.init_job.handler.handler"]
  }

  environment {
    variables = local.lambda_env
  }
}

resource "aws_lambda_function_url" "init_job" {
  function_name      = aws_lambda_function.init_job.function_name
  authorization_type = "NONE"
}

# --- orchestrator ---

resource "aws_lambda_function" "orchestrator" {
  function_name = "iac-ci-orchestrator"
  role          = aws_iam_role.orchestrator.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  timeout       = 600
  memory_size   = 512

  image_config {
    command = ["src.orchestrator.handler.handler"]
  }

  environment {
    variables = merge(local.lambda_env, {
      IAC_CI_WORKER_LAMBDA     = "iac-ci-worker"
      IAC_CI_CODEBUILD_PROJECT = aws_codebuild_project.worker.name
      IAC_CI_WATCHDOG_SFN      = aws_sfn_state_machine.watchdog.arn
    })
  }
}

# --- watchdog_check ---

resource "aws_lambda_function" "watchdog_check" {
  function_name = "iac-ci-watchdog-check"
  role          = aws_iam_role.watchdog_check.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  timeout       = 60
  memory_size   = 256

  image_config {
    command = ["src.watchdog_check.handler.handler"]
  }

  environment {
    variables = local.lambda_env
  }
}

# --- worker ---

resource "aws_lambda_function" "worker" {
  function_name = "iac-ci-worker"
  role          = aws_iam_role.worker.arn
  package_type  = "Image"
  image_uri     = local.image_uri
  timeout       = 600
  memory_size   = 1024

  image_config {
    command = ["src.worker.handler.handler"]
  }

  environment {
    variables = local.lambda_env
  }
}
