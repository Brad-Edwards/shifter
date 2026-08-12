variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "gke_provisioner_pods_cidr" {
  type = string
}

variable "range_provisioner_ports" {
  type = list(number)
}

variable "operator_admin_cidrs" {
  type = list(string)
}

variable "range_egress_mode" {
  type = string
}

variable "range_egress_allowed_cidrs" {
  type = list(string)
}

variable "range_network_zones" {
  type    = list(string)
  default = []

  description = <<-EOT
    #2029 multi-region range placement: the same fully-qualified GCE zone pool the
    provisioner places range cells with (the RANGE_NETWORK_ZONES runtime value,
    passed here as a list from one operator input). Every region other than the
    primary `region` that a pooled zone lives in gets its own Cloud Router +
    external address + Cloud NAT, so NAT coverage is derived from the pool and
    cannot diverge from it. Empty keeps single-region behaviour (only the primary
    region's NAT exists).
  EOT

  validation {
    condition     = alltrue([for z in var.range_network_zones : can(regex("^[a-z]+-[a-z]+[0-9]+-[a-z]$", z))])
    error_message = "range_network_zones must be a list of fully-qualified GCE zones (e.g. 'us-central1-a')."
  }
}
