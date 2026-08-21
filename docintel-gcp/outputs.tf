output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docintel.repository_id}"
}

output "backend_service_name" {
  value = local.service_name
}

output "region" {
  value = var.region
}

output "backend_url" {
  value = local.deploy_app ? google_cloud_run_v2_service.backend[0].uri : null
}

output "database_connection_name" {
  value = google_sql_database_instance.docintel.connection_name
}

output "document_bucket" {
  value = google_storage_bucket.documents.name
}

output "firebase_site" {
  value = "https://${var.firebase_site_id}.web.app"
}

output "next_step" {
  value = local.deploy_app ? "Run scripts/deploy-frontend.sh, then scripts/validate.sh." : "Build and push the backend image, then apply again with -var=backend_image=IMAGE_URI."
}
