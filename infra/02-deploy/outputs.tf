output "api_gateway_url" {
  description = "API Gateway endpoint URL"
  value       = aws_apigatewayv2_api.webhook.api_endpoint
}

output "api_gateway_id" {
  description = "API Gateway ID"
  value       = aws_apigatewayv2_api.webhook.id
}

output "lambda_function_names" {
  description = "Map of Lambda function names"
  value = {
    init_job        = aws_lambda_function.init_job.function_name
    orchestrator    = aws_lambda_function.orchestrator.function_name
    watchdog_check  = aws_lambda_function.watchdog_check.function_name
    worker          = aws_lambda_function.worker.function_name
  }
}

output "lambda_function_arns" {
  description = "Map of Lambda function ARNs"
  value = {
    init_job        = aws_lambda_function.init_job.arn
    orchestrator    = aws_lambda_function.orchestrator.arn
    watchdog_check  = aws_lambda_function.watchdog_check.arn
    worker          = aws_lambda_function.worker.arn
  }
}

output "dynamodb_table_names" {
  description = "Map of DynamoDB table names"
  value = {
    orders             = aws_dynamodb_table.orders.name
    order_events       = aws_dynamodb_table.order_events.name
    orchestrator_locks = aws_dynamodb_table.orchestrator_locks.name
  }
}

output "s3_bucket_names" {
  description = "Map of S3 bucket names"
  value = {
    internal = aws_s3_bucket.internal.id
    done     = aws_s3_bucket.done.id
  }
}

output "step_function_arn" {
  description = "Watchdog Step Function ARN"
  value       = aws_sfn_state_machine.watchdog.arn
}

output "codebuild_project_name" {
  description = "CodeBuild project name"
  value       = aws_codebuild_project.worker.name
}
