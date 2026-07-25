# Environment-varying inputs.
#
# These were hardcoded per environment before the composition was extracted
# (#688). They travel as explicit typed inputs so posture is never inferred
# from the environment name; each root passes the value it used before.

variable "alb_enable_deletion_protection" {
  description = "Enable ALB deletion protection. Disposable environments set false to allow terraform destroy."
  type        = bool
}

variable "cognito_deletion_protection" {
  description = "Enable Cognito user pool deletion protection."
  type        = bool
}

variable "cognito_access_token_validity_hours" {
  description = "Cognito access token validity in hours."
  type        = number
}

variable "cognito_id_token_validity_hours" {
  description = "Cognito ID token validity in hours."
  type        = number
}

variable "secret_recovery_window_in_days" {
  description = "Secrets Manager recovery window for the app secret. 0 deletes immediately, which disposable environments use to avoid naming conflicts on recreate."
  type        = number
}

variable "engine_enable_alarms" {
  description = "Enable engine-provisioner CloudWatch alarms."
  type        = bool
}

variable "engine_alarm_email" {
  description = "Email address for engine-provisioner alarm notifications."
  type        = string
}

variable "log_aggregation_enable_alarms" {
  description = "Enable log-aggregation CloudWatch alarms."
  type        = bool
}

variable "log_aggregation_alarm_email" {
  description = "Email address for log-aggregation alarm notifications."
  type        = string
}
