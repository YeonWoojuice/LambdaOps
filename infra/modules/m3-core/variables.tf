variable "service" {
  description = "Service monitored by the M2 alarms, for example slash-api."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,100}$", var.service))
    error_message = "service must contain 1-100 letters, digits, underscores, or hyphens."
  }
}

variable "environment" {
  description = "Environment monitored by the M2 alarms, for example dev."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,32}$", var.environment))
    error_message = "environment must contain 1-32 letters, digits, underscores, or hyphens."
  }
}

variable "alarm_arns" {
  description = "The alarm_arns map output by the M2 module. All three alarms must belong to the current AWS account and region."
  type        = map(string)
  default     = {}

  validation {
    condition = length(var.alarm_arns) == 0 || (
      length(var.alarm_arns) == 3 &&
      contains(keys(var.alarm_arns), "api_5xx_rate") &&
      contains(keys(var.alarm_arns), "api_average_latency") &&
      contains(keys(var.alarm_arns), "db_average_query_latency")
    )
    error_message = "alarm_arns must be empty before M2 connection, or contain exactly the three M2 alarm_arns output keys."
  }

  validation {
    condition     = alltrue([for arn in values(var.alarm_arns) : can(regex("^arn:[^:]+:cloudwatch:[^:]+:[0-9]{12}:alarm:.+$", arn))])
    error_message = "Each alarm ARN must be a CloudWatch alarm ARN."
  }
}

variable "ingest_package_zip_path" {
  description = "Path to the ZIP containing the lambdaops_m3 package for the SQS ingest Lambda."
  type        = string

  validation {
    condition     = can(regex("\\.zip$", var.ingest_package_zip_path))
    error_message = "ingest_package_zip_path must point to a .zip file."
  }
}

variable "read_package_zip_path" {
  description = "Path to the ZIP containing the lambdaops_m3 package for the HTTP read Lambda."
  type        = string

  validation {
    condition     = can(regex("\\.zip$", var.read_package_zip_path))
    error_message = "read_package_zip_path must point to a .zip file."
  }
}

variable "jwt_issuer" {
  description = "HTTPS issuer URL for API Gateway's JWT authorizer."
  type        = string
  default     = null

  validation {
    condition     = var.jwt_issuer == null ? true : can(regex("^https://[^[:space:]]+$", var.jwt_issuer))
    error_message = "jwt_issuer must be an HTTPS URL."
  }
}

variable "jwt_audience" {
  description = "Expected audience claim for API Gateway's JWT authorizer."
  type        = string
  default     = null

  validation {
    condition     = var.jwt_audience == null ? true : length(trimspace(var.jwt_audience)) > 0
    error_message = "jwt_audience must not be empty."
  }
}

variable "read_authorization_scope" {
  description = "JWT scope required to read incidents. API Gateway and the read handler enforce the same scope."
  type        = string
  default     = "incidents:read"

  validation {
    condition     = length(trimspace(var.read_authorization_scope)) > 0
    error_message = "read_authorization_scope must not be empty."
  }
}

variable "ingest_timeout_seconds" {
  description = "Timeout of the ingest Lambda. Source queue visibility is six times this value."
  type        = number
  default     = 30

  validation {
    condition     = var.ingest_timeout_seconds >= 1 && var.ingest_timeout_seconds <= 900 && floor(var.ingest_timeout_seconds) == var.ingest_timeout_seconds
    error_message = "ingest_timeout_seconds must be a whole number from 1 to 900."
  }
}

variable "tags" {
  description = "Tags applied to supported M3 resources."
  type        = map(string)
  default     = {}
}

variable "incident_store" {
  description = "Incident database adapter. DynamoDB is retained only for explicit compatibility deployments."
  type        = string
  default     = "dsql"

  validation {
    condition     = contains(["dsql", "dynamodb"], var.incident_store)
    error_message = "incident_store must be dsql or dynamodb."
  }
}

variable "dsql_schema_ready" {
  description = "Set true only after approved schema, ASYNC indexes, IAM mappings and SQL privileges are verified."
  type        = bool
  default     = false
}

variable "connect_m2_alarms" {
  description = "Connect only real deployed M2 alarm outputs. False permits DB/Lambda provisioning before M2."
  type        = bool
  default     = false
}

variable "ingestion_enabled" {
  description = "Allow EventBridge capture and SQS consumption only when M2 is connected and storage is ready."
  type        = bool
  default     = true
}

variable "enable_http_api" {
  description = "Create the JWT-protected HTTP API after storage is ready. Requires real issuer and audience."
  type        = bool
  default     = false
}

variable "dsql_ingest_db_user" {
  description = "Custom DSQL role mapped exclusively to the ingest Lambda IAM role."
  type        = string
  default     = "lambdaops_ingest"

  validation {
    condition     = can(regex("^[a-z_][a-z0-9_]{0,62}$", var.dsql_ingest_db_user)) && !contains(["admin", "postgres", "public"], var.dsql_ingest_db_user)
    error_message = "dsql_ingest_db_user must be a custom lowercase SQL identifier, not admin, postgres, or public."
  }
}

variable "dsql_read_db_user" {
  description = "Custom DSQL role mapped exclusively to the read Lambda IAM role."
  type        = string
  default     = "lambdaops_read"

  validation {
    condition     = can(regex("^[a-z_][a-z0-9_]{0,62}$", var.dsql_read_db_user)) && !contains(["admin", "postgres", "public"], var.dsql_read_db_user)
    error_message = "dsql_read_db_user must be a custom lowercase SQL identifier, not admin, postgres, or public."
  }
}

variable "read_timeout_seconds" {
  description = "Read Lambda timeout, including IAM database connection and snapshot queries."
  type        = number
  default     = 30

  validation {
    condition     = var.read_timeout_seconds >= 1 && var.read_timeout_seconds <= 900 && floor(var.read_timeout_seconds) == var.read_timeout_seconds
    error_message = "read_timeout_seconds must be a whole number from 1 to 900."
  }
}

variable "ingest_batch_size" {
  description = "SQS batch size. One limits sequential DSQL connection work within the Lambda timeout."
  type        = number
  default     = 1

  validation {
    condition     = var.ingest_batch_size >= 1 && var.ingest_batch_size <= 10 && floor(var.ingest_batch_size) == var.ingest_batch_size
    error_message = "ingest_batch_size must be a whole number from 1 to 10."
  }
}
