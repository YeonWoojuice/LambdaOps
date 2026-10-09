variable "service" {
  description = "Service monitored by the M2 alarms, for example slash-api."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.service))
    error_message = "service must contain 1-64 letters, digits, underscores, or hyphens."
  }
}

variable "environment" {
  description = "Environment monitored by the M2 alarms, for example dev."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.environment))
    error_message = "environment must contain 1-64 letters, digits, underscores, or hyphens."
  }
}

variable "alarm_arns" {
  description = "The alarm_arns map output by the M2 module. All three alarms must belong to the current AWS account and region."
  type        = map(string)

  validation {
    condition = (
      length(var.alarm_arns) == 3 &&
      contains(keys(var.alarm_arns), "api_5xx_rate") &&
      contains(keys(var.alarm_arns), "api_average_latency") &&
      contains(keys(var.alarm_arns), "db_average_query_latency")
    )
    error_message = "alarm_arns must contain exactly the three keys from the M2 alarm_arns output."
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

  validation {
    condition     = can(regex("^https://[^ ]+$", var.jwt_issuer))
    error_message = "jwt_issuer must be an HTTPS URL."
  }
}

variable "jwt_audience" {
  description = "Expected audience claim for API Gateway's JWT authorizer."
  type        = string

  validation {
    condition     = length(trimspace(var.jwt_audience)) > 0
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
