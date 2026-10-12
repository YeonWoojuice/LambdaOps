variable "aws_account_id" {
  description = "The 12-digit AWS account ID allowed for the dev deployment."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit AWS account ID."
  }
}

variable "aws_region" {
  description = "AWS region that hosts Slash metrics and these alarms."
  type        = string

  validation {
    condition     = length(trimspace(var.aws_region)) > 0
    error_message = "aws_region must not be empty."
  }
}

variable "service" {
  description = "Actual Service dimension emitted by the deployed metric publisher."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.service))
    error_message = "service must contain 1-64 letters, digits, underscores, or hyphens."
  }
}

variable "environment" {
  description = "Actual Environment dimension emitted by the deployed metric publisher."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.environment))
    error_message = "environment must contain 1-64 letters, digits, underscores, or hyphens."
  }
}

variable "api_5xx_rate_threshold_percent" {
  description = "Measured API 5xx rate threshold in percent; choose from dev traffic."
  type        = number
}

variable "api_average_latency_threshold_ms" {
  description = "Measured mean API latency threshold in milliseconds."
  type        = number
}

variable "db_average_query_latency_threshold_ms" {
  description = "Measured mean DB query latency threshold in milliseconds."
  type        = number
}

variable "minimum_requests_per_period" {
  description = "Measured minimum completed API requests per 60-second interval."
  type        = number
}

variable "minimum_db_queries_per_period" {
  description = "Measured minimum completed DB queries per 60-second interval."
  type        = number
}
