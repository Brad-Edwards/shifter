# Portal root variables - storage.
#
# Split out of variables.tf by concern (#688). Names, types, defaults
# and validation are unchanged; the roots remain authoritative for the
# public input contract.

# ------------------------------------------------------------------------------
# S3
# ------------------------------------------------------------------------------


variable "user_storage_bucket" {
  description = "S3 bucket name for user file storage (must be globally unique)"
  type        = string
}
