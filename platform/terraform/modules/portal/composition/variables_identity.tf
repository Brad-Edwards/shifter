# Portal composition inputs - identity.
#
# Types match the environment-root declarations exactly. Defaults and
# operator-facing validation stay authoritative at the roots, so there is
# no second drifting schema here (#688).

variable "cognito_domain_prefix" {
  description = "Domain prefix for Cognito hosted UI (must be globally unique)"
  type        = string
}
