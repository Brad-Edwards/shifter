# Portal composition inputs - storage.
#
# Types match the environment-root declarations exactly. Defaults and
# operator-facing validation stay authoritative at the roots, so there is
# no second drifting schema here (#688).

variable "aces_package_bucket_arn" {
  description = "ARN of the S3 bucket holding object-backed ACES package archives (#1567). Grants the portal role read-only access; set it (with SHIFTER_ACES_PACKAGE_BUCKET on the app) to enable object-backed ACES packages. Empty disables the grant."
  type        = string
}

variable "aces_package_prefix" {
  description = "Optional key prefix under the ACES package bucket the portal may read (least-privilege scoping)."
  type        = string
}

variable "user_storage_bucket" {
  description = "S3 bucket name for user file storage (must be globally unique)"
  type        = string
}
