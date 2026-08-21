locals {
  prefix       = "docintel-${var.environment}"
  deploy_app   = trimspace(var.backend_image) != ""
  db_name      = "docintel"
  db_user      = "docintel"
  bucket_name  = "${var.project_id}-${local.prefix}-documents"
  service_name = "${local.prefix}-backend"

  required_apis = toset([
    "aiplatform.googleapis.com",
    "apikeys.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "compute.googleapis.com",
    "firebase.googleapis.com",
    "firebasehosting.googleapis.com",
    "generativelanguage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "speech.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
  ])

  runtime_env = {
    LLM_PROVIDER                         = "gemini"
    EMBEDDING_DIM                        = "768"
    GEMINI_EMBED_MODEL                   = "gemini-embedding-2"
    GEMINI_CHAT_MODEL                    = "gemini-2.5-flash"
    OPENAI_EMBED_MODEL                   = "text-embedding-3-small"
    OPENAI_CHAT_MODEL                    = "gpt-4o-mini"
    CHUNK_SIZE                           = "350"
    CHUNK_OVERLAP                        = "60"
    TOP_K                                = "6"
    MAX_UPLOAD_FILES                     = "500"
    MAX_FILE_SIZE_MB                     = "50"
    VIDEO_TRANSCRIBE_AUDIO_ENABLED       = "true"
    VIDEO_TRANSCRIBE_PROVIDER            = "google_speech"
    VIDEO_TRANSCRIBE_LANGUAGE_CODE       = "en-US"
    VIDEO_TRANSCRIBE_CHUNK_SECONDS       = "55"
    VIDEO_TRANSCRIBE_CONCURRENCY         = "3"
    VIDEO_FRAME_CONCURRENCY              = "4"
    VIDEO_EMBED_CONCURRENCY              = "4"
    GOOGLE_SPEECH_MODEL                  = "latest_long"
    VIDEO_MAX_FRAMES                     = "12"
    VIDEO_SEGMENT_SECONDS                = "60"
    VIDEO_FRAME_CAPTION_ENABLED          = "true"
    VIDEO_SOURCE_READ_URL_EXPIRY_SECONDS = "21600"
    FFMPEG_REMOTE_TIMEOUT_US             = "30000000"
    VIDEO_REMOTE_STAGE_RETRIES           = "3"
    VIDEO_REMOTE_RETRY_DELAY_SECONDS     = "3"
    FFMPEG_COMMAND_TIMEOUT_SECONDS       = "180"
    JWT_ALGORITHM                        = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES      = "480"
    GCS_SIGNED_URL_EXPIRY_SECONDS        = "3600"
    APP_URL                              = var.app_url
    EMAIL_FROM_NAME                      = "আদর DocIntel"
    RESET_TOKEN_EXPIRE_HOURS             = "1"
    MFA_ENABLED                          = "true"
    SKIP_EMAIL_VERIFICATION              = "false"
    RERANK_ENABLED                       = "true"
    RERANK_FETCH_K                       = "20"
    RRF_K                                = "60"
    GUEST_MAX_UPLOADS                    = "3"
    GUEST_MAX_QUERIES                    = "5"
    GUEST_MAX_FILE_MB                    = "20"
    GUEST_SESSION_TTL_HOURS              = "24"
  }
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_service" "required" {
  for_each           = local.required_apis
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "docintel" {
  location      = var.region
  repository_id = "docintel"
  format        = "DOCKER"
  description   = "DocIntel application images"
  labels        = var.labels
  depends_on    = [google_project_service.required]
}

resource "google_compute_network" "docintel" {
  name                    = "${local.prefix}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}

resource "google_compute_subnetwork" "docintel" {
  name                     = "${local.prefix}-subnet"
  region                   = var.region
  network                  = google_compute_network.docintel.id
  ip_cidr_range            = "10.42.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_global_address" "private_services" {
  name          = "${local.prefix}-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.docintel.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.docintel.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
  depends_on              = [google_project_service.required]
}

resource "random_password" "database" {
  length  = 32
  special = false
}

resource "random_password" "jwt" {
  length  = 64
  special = false
}

resource "google_sql_database_instance" "docintel" {
  name                = "${local.prefix}-db"
  region              = var.region
  database_version    = "POSTGRES_15"
  deletion_protection = var.deletion_protection

  settings {
    tier              = var.db_tier
    availability_type = var.db_high_availability ? "REGIONAL" : "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 20
    disk_autoresize   = true
    user_labels       = var.labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.docintel.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "03:00"
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 14
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = 7
      hour         = 4
      update_track = "stable"
    }
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "docintel" {
  name     = local.db_name
  instance = google_sql_database_instance.docintel.name
}

resource "google_sql_user" "docintel" {
  name     = local.db_user
  instance = google_sql_database_instance.docintel.name
  password = random_password.database.result
}

resource "google_storage_bucket" "documents" {
  name                        = local.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  labels                      = var.labels

  versioning { enabled = true }

  lifecycle_rule {
    condition { num_newer_versions = 3 }
    action { type = "Delete" }
  }

  cors {
    origin          = [var.app_url]
    method          = ["GET", "HEAD", "PUT", "POST", "DELETE"]
    response_header = ["Content-Type", "ETag", "x-goog-resumable"]
    max_age_seconds = 3600
  }

  depends_on = [google_project_service.required]
}

resource "google_service_account" "api" {
  account_id   = "${local.prefix}-api"
  display_name = "DocIntel API runtime"
}

resource "google_service_account" "build" {
  account_id   = "${local.prefix}-build"
  display_name = "DocIntel Cloud Build"
}

locals {
  api_roles = toset([
    "roles/aiplatform.user",
    "roles/cloudsql.client",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/speech.client",
    "roles/storage.objectAdmin",
  ])
}

resource "google_project_iam_member" "api" {
  for_each = local.api_roles
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.api.email}"
}

resource "google_service_account_iam_member" "api_can_sign" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "build_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.build.email}"
}

resource "google_project_iam_member" "cloud_build_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
}

resource "google_project_iam_member" "compute_build_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_apikeys_key" "gemini" {
  name         = "${local.prefix}-gemini"
  display_name = "DocIntel Gemini API key"

  restrictions {
    api_targets { service = "generativelanguage.googleapis.com" }
  }

  depends_on = [google_project_service.required]
}

resource "google_apikeys_key" "speech" {
  name         = "${local.prefix}-speech"
  display_name = "DocIntel Speech API key"

  restrictions {
    api_targets { service = "speech.googleapis.com" }
  }

  depends_on = [google_project_service.required]
}

locals {
  required_secrets = {
    "docintel-jwt-secret"        = random_password.jwt.result
    "docintel-db-password"       = random_password.database.result
    "docintel-database-url"      = "postgresql://${local.db_user}:${random_password.database.result}@/${local.db_name}?host=/cloudsql/${google_sql_database_instance.docintel.connection_name}"
    "docintel-gcs-bucket"        = google_storage_bucket.documents.name
    "docintel-gemini-key"        = google_apikeys_key.gemini.key_string
    "docintel-google-speech-key" = google_apikeys_key.speech.key_string
    "docintel-llm-provider"      = "gemini"
    "docintel-embedding-dim"     = "768"
  }

  optional_secrets = merge(
    var.gmail_user != "" && var.gmail_app_password != "" ? {
      "docintel-gmail-user"         = var.gmail_user
      "docintel-gmail-app-password" = var.gmail_app_password
    } : {},
    var.openai_api_key != "" ? { "docintel-openai-key" = var.openai_api_key } : {},
    var.cohere_api_key != "" ? { "docintel-cohere-key" = var.cohere_api_key } : {},
    var.stripe_secret_key != "" ? { "docintel-stripe-secret-key" = var.stripe_secret_key } : {},
    var.stripe_webhook_secret != "" ? { "docintel-stripe-webhook-secret" = var.stripe_webhook_secret } : {},
    var.stripe_restaurant_webhook_secret != "" ? { "docintel-stripe-restaurant-webhook-secret" = var.stripe_restaurant_webhook_secret } : {},
    var.stripe_pro_price_id != "" ? { "docintel-stripe-pro-price-id" = var.stripe_pro_price_id } : {},
    var.stripe_enterprise_price_id != "" ? { "docintel-stripe-enterprise-price-id" = var.stripe_enterprise_price_id } : {},
  )

  optional_secret_env_names = {
    "docintel-gmail-user"                       = "GMAIL_USER"
    "docintel-gmail-app-password"               = "GMAIL_APP_PASSWORD"
    "docintel-openai-key"                       = "OPENAI_API_KEY"
    "docintel-cohere-key"                       = "COHERE_API_KEY"
    "docintel-stripe-secret-key"                = "STRIPE_SECRET_KEY"
    "docintel-stripe-webhook-secret"            = "STRIPE_WEBHOOK_SECRET"
    "docintel-stripe-restaurant-webhook-secret" = "STRIPE_RESTAURANT_WEBHOOK_SECRET"
    "docintel-stripe-pro-price-id"              = "STRIPE_PRO_PRICE_ID"
    "docintel-stripe-enterprise-price-id"       = "STRIPE_ENTERPRISE_PRICE_ID"
  }

  all_secrets = merge(local.required_secrets, local.optional_secrets)
}

resource "google_secret_manager_secret" "docintel" {
  for_each  = local.all_secrets
  secret_id = each.key
  labels    = var.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "docintel" {
  for_each    = local.all_secrets
  secret      = google_secret_manager_secret.docintel[each.key].id
  secret_data = each.value
}

resource "google_secret_manager_secret_iam_member" "api" {
  for_each  = local.all_secrets
  secret_id = google_secret_manager_secret.docintel[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_firebase_project" "docintel" {
  provider   = google-beta
  project    = var.project_id
  depends_on = [google_project_service.required]
}

resource "google_firebase_hosting_site" "docintel" {
  provider   = google-beta
  project    = var.project_id
  site_id    = var.firebase_site_id
  depends_on = [google_firebase_project.docintel]
}

resource "google_cloud_run_v2_service" "backend" {
  count    = local.deploy_app ? 1 : 0
  name     = local.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"
  labels   = var.labels

  template {
    service_account                  = google_service_account.api.email
    timeout                          = "3600s"
    max_instance_request_concurrency = 80

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.backend_image

      resources {
        limits            = { cpu = "2", memory = "4Gi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      ports { container_port = 8080 }

      dynamic "env" {
        for_each = local.runtime_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = {
          JWT_SECRET_KEY        = "docintel-jwt-secret"
          DATABASE_URL          = "docintel-database-url"
          GCS_BUCKET_NAME       = "docintel-gcs-bucket"
          GOOGLE_AI_KEY         = "docintel-gemini-key"
          GOOGLE_SPEECH_API_KEY = "docintel-google-speech-key"
        }
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.docintel[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      dynamic "env" {
        for_each = local.optional_secrets
        content {
          name = local.optional_secret_env_names[env.key]
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.docintel[env.key].secret_id
              version = "latest"
            }
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      startup_probe {
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 10
        failure_threshold     = 30
        http_get { path = "/api/health" }
      }

      liveness_probe {
        timeout_seconds   = 5
        period_seconds    = 30
        failure_threshold = 3
        http_get { path = "/api/health" }
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.docintel.connection_name]
      }
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.docintel.name
        subnetwork = google_compute_subnetwork.docintel.name
      }
      egress = "PRIVATE_RANGES_ONLY"
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.api,
    google_project_iam_member.api,
    google_sql_user.docintel,
  ]
}

# Firebase Hosting invokes the API without Google identity; DocIntel enforces
# application authentication and workspace authorization at the API layer.
resource "google_cloud_run_v2_service_iam_member" "public_api" {
  count    = local.deploy_app ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.backend[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
