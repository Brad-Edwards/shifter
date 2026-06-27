variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "identity_authorized_domains" {
  type = list(string)
}

variable "identity_allowed_email_domain" {
  type = string
}

variable "identity_allowed_emails" {
  type = list(string)
}

variable "assets_bucket_name" {
  type = string
}
