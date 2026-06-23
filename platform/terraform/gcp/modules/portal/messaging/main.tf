resource "google_pubsub_topic" "platform_events" {
  name    = "${var.name_prefix}-events"
  project = var.project_id
  labels  = var.common_labels
}

resource "google_pubsub_subscription" "platform_events" {
  for_each = var.platform_event_subscriptions

  name                       = "${var.name_prefix}-${each.key}"
  project                    = var.project_id
  topic                      = google_pubsub_topic.platform_events.name
  ack_deadline_seconds       = 20
  message_retention_duration = "604800s"
  labels                     = merge(var.common_labels, { worker = each.key })
}
