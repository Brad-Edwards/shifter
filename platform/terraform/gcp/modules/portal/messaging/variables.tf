variable "project_id" {
  type = string
}

variable "name_prefix" {
  type = string
}

variable "common_labels" {
  type = map(string)
}

variable "platform_event_subscriptions" {
  type = set(string)
}
