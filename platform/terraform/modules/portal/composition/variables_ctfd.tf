# CTFd inputs.
#
# Feature-gated by enable_ctfd: module.ctfd carries count = enable_ctfd ? 1 : 0,
# so these are never evaluated when CTFd is off. They keep the dev root's
# declared types, with empty defaults so a root that does not run CTFd - prod -
# can omit them entirely rather than growing a variable contract for a
# feature it does not deploy (#688). Roots that DO run CTFd pass every value
# explicitly from their own required variables, so the empty defaults are
# never the deployed configuration.

variable "ctfd_ami_id" {
  description = "AMI ID for the CTFd instance"
  type        = string
  default     = ""
}

variable "ctfd_docker_buildx_version" {
  description = "Pinned Docker Buildx release tag for CTFd"
  type        = string
  default     = ""
}

variable "ctfd_docker_compose_version" {
  description = "Pinned Docker Compose release tag for CTFd"
  type        = string
  default     = ""
}

variable "ctfd_domain" {
  description = "Public DNS name for the dev CTFd host"
  type        = string
  default     = ""
}

variable "ctfd_git_ref" {
  description = "Pinned CTFd git ref to deploy"
  type        = string
  default     = ""
}

variable "ctfd_instance_type" {
  description = "EC2 instance type for CTFd"
  type        = string
  default     = ""
}

variable "ctfd_repo_url" {
  description = "CTFd git repository URL"
  type        = string
  default     = ""
}

variable "ctfd_root_volume_iops" {
  description = "Root volume IOPS for the CTFd instance"
  type        = number
  default     = 0
}

variable "ctfd_root_volume_size" {
  description = "Root volume size for the CTFd instance in GB"
  type        = number
  default     = 0
}

variable "ctfd_root_volume_throughput" {
  description = "Root volume throughput in MiB/s for the CTFd instance"
  type        = number
  default     = 0
}

variable "ctfd_root_volume_type" {
  description = "Root volume type for the CTFd instance"
  type        = string
  default     = ""
}

variable "ctfd_ssh_allowed_cidrs" {
  description = "Map of allowed SSH source CIDRs for the CTFd host"
  type        = map(string)
  default     = {}
}

variable "ctfd_ssh_public_key" {
  description = "SSH public key material for direct SSH access to the CTFd host"
  type        = string
  default     = ""
}
