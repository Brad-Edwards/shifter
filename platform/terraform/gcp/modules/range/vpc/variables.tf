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

variable "shared_range_nat_subnetwork_self_links" {
  type        = list(string)
  default     = []
  description = <<-EOT
    PLAT-238 / ADR-026-R6 migration bridge. Self-links of pre-migration range
    subnets the shared range Cloud NAT should still enroll (LIST_OF_SUBNETWORKS)
    while they are drained onto per-range provisioner-owned NAT. Empty (the
    default) means the shared NAT enrolls no subnets: every range's egress is
    range-owned, and a zero-egress (`none`) range has no NAT path at all.
  EOT
}
