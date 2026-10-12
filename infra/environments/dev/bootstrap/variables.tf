variable "state_account_id" {
  description = "12-digit AWS account ID that will own the Terraform state resources."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.state_account_id))
    error_message = "state_account_id must be a 12-digit AWS account ID."
  }
}

variable "state_region" {
  description = "AWS region for the state bucket and lock table."
  type        = string

  validation {
    condition     = length(trimspace(var.state_region)) > 0
    error_message = "state_region must not be empty."
  }
}

variable "state_bucket_name" {
  description = "Globally unique name for the S3 bucket holding Terraform state."
  type        = string

  validation {
    condition     = length(var.state_bucket_name) >= 3 && length(var.state_bucket_name) <= 63
    error_message = "state_bucket_name must be 3-63 characters; AWS enforces the remaining S3 naming rules."
  }
}

variable "lock_table_name" {
  description = "Name of the DynamoDB LockID table for Terraform 1.5.7 state locking."
  type        = string

  validation {
    condition     = length(trimspace(var.lock_table_name)) >= 3
    error_message = "lock_table_name must have at least three characters."
  }
}

variable "tags" {
  description = "Optional tags for the state bucket and lock table."
  type        = map(string)
  default     = {}
}
