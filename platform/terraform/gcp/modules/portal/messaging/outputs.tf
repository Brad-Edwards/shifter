output "platform_events_topic_id" {
  description = "Shared Pub/Sub topic for platform lifecycle and experiment events."
  value       = google_pubsub_topic.platform_events.id
}

output "platform_event_subscriptions" {
  description = "Pub/Sub subscriptions keyed by worker role."
  value = {
    for name, subscription in google_pubsub_subscription.platform_events :
    name => subscription.id
  }
}
