variable "service" {
  description = "Service dimension of all five source metrics, for example slash-api."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.service))
    error_message = "service must contain 1-64 letters, digits, underscores, or hyphens."
  }
}

variable "environment" {
  description = "Environment dimension of all five source metrics, for example dev."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,64}$", var.environment))
    error_message = "environment must contain 1-64 letters, digits, underscores, or hyphens."
  }
}

variable "api_5xx_rate_threshold_percent" {
  description = "Maximum acceptable API 5xx percentage in a 60-second period; choose after observing traffic."
  type        = number

  validation {
    condition     = var.api_5xx_rate_threshold_percent > 0 && var.api_5xx_rate_threshold_percent <= 100
    error_message = "api_5xx_rate_threshold_percent must be greater than 0 and at most 100."
  }
}

variable "api_average_latency_threshold_ms" {
  description = "Maximum acceptable mean API response duration, in milliseconds; choose after observing traffic."
  type        = number

  validation {
    condition     = var.api_average_latency_threshold_ms > 0
    error_message = "api_average_latency_threshold_ms must be greater than 0."
  }
}

variable "db_average_query_latency_threshold_ms" {
  description = "Maximum acceptable mean DB query duration, in milliseconds; choose after observing traffic."
  type        = number

  validation {
    condition     = var.db_average_query_latency_threshold_ms > 0
    error_message = "db_average_query_latency_threshold_ms must be greater than 0."
  }
}

variable "minimum_requests_per_period" {
  description = "Minimum RequestCount Sum in a 60-second period before either API alarm can breach."
  type        = number

  validation {
    condition     = var.minimum_requests_per_period >= 1 && floor(var.minimum_requests_per_period) == var.minimum_requests_per_period
    error_message = "minimum_requests_per_period must be a positive whole number."
  }
}

variable "minimum_db_queries_per_period" {
  description = "Minimum DBQueryCount Sum in a 60-second period before the DB alarm can breach."
  type        = number

  validation {
    condition     = var.minimum_db_queries_per_period >= 1 && floor(var.minimum_db_queries_per_period) == var.minimum_db_queries_per_period
    error_message = "minimum_db_queries_per_period must be a positive whole number."
  }
}

variable "evaluation_periods" {
  description = "Number of 60-second periods evaluated by each alarm."
  type        = number
  default     = 3

  validation {
    condition     = var.evaluation_periods >= 1 && floor(var.evaluation_periods) == var.evaluation_periods
    error_message = "evaluation_periods must be a positive whole number."
  }
}

variable "datapoints_to_alarm" {
  description = "Number of breaching periods needed; default is two out of three."
  type        = number
  default     = 2

  validation {
    condition     = var.datapoints_to_alarm >= 1 && floor(var.datapoints_to_alarm) == var.datapoints_to_alarm
    error_message = "datapoints_to_alarm must be a positive whole number."
  }
}

variable "tags" {
  description = "Tags applied to the three alarms."
  type        = map(string)
  default     = {}
}
