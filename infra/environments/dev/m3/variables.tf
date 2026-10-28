variable "aws_account_id" {
  description = "The 12-digit AWS account ID allowed for the dev deployment."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit AWS account ID."
  }
}

variable "aws_region" {
  description = "AWS region for the dev M2 alarms and M3 resources."
  type        = string

  validation {
    condition     = length(trimspace(var.aws_region)) > 0
    error_message = "aws_region must not be empty."
  }
}

variable "service" {
  description = "Actual Service dimension from the deployed M2 alarms; must match the M2 root input."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.service))
    error_message = "service must contain 1-64 letters, digits, underscores, or hyphens."
  }
}

variable "environment" {
  description = "Actual Environment dimension from the deployed M2 alarms; must match the M2 root input."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,32}$", var.environment))
    error_message = "environment must contain 1-32 letters, digits, underscores, or hyphens (DSQL column limit)."
  }
}

variable "m2_state_bucket" {
  description = "Existing S3 bucket containing the deployed M2 root's state."
  type        = string
  default     = null
}

variable "m2_state_key" {
  description = "Exact S3 key used by the deployed M2 root's backend."
  type        = string
  default     = "lambdaops/dev/m2.tfstate"
}

variable "m2_state_region" {
  description = "Region of the existing S3 state bucket; this may differ from the deployment region."
  type        = string
  default     = null
}

variable "ingest_package_zip_path" {
  description = "Path to the built Python 3.12 Lambda ZIP containing lambdaops_m3 at its root."
  type        = string
}

variable "read_package_zip_path" {
  description = "Path to the built Python 3.12 read Lambda ZIP; it may be the same as the ingest ZIP."
  type        = string
}

variable "jwt_issuer" {
  description = "Existing OpenID Connect issuer URL for JWT validation."
  type        = string
  default     = null
}

variable "jwt_audience" {
  description = "Expected audience or client ID of the JWT access token."
  type        = string
  default     = null
}

variable "incident_store" {
  description = "Selected Incident backend. DSQL is the approved default; dynamodb is compatibility only."
  type        = string
  default     = "dsql"
  validation {
    condition     = contains(["dsql", "dynamodb"], var.incident_store)
    error_message = "incident_store must be dsql or dynamodb."
  }
}

variable "connect_m2_alarms" {
  description = "Read the deployed M2 state and create EventBridge routing. Leave false while M2 is unavailable."
  type        = bool
  default     = false
}

variable "dsql_schema_ready" {
  description = "Set true only after schema, ASYNC indexes, SQL grants and IAM mappings pass bootstrap verification."
  type        = bool
  default     = false
}

variable "ingestion_enabled" {
  description = "Enable automatic ingestion only when M2 and the database readiness gates also pass."
  type        = bool
  default     = true
}

variable "enable_http_api" {
  description = "Create the JWT-protected HTTP API only after real issuer/audience and database readiness are configured."
  type        = bool
  default     = false
}

variable "read_authorization_scope" {
  description = "Scope actually issued by the identity provider for incident reads."
  type        = string
  default     = "incidents:read"
}
