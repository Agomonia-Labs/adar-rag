variable "project_id" {
  description = "Customer-owned GCP project with billing enabled."
  type        = string
}

variable "region" {
  description = "Primary GCP region for DocIntel."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name used in resource names."
  type        = string
  default     = "prod"
  validation {
    condition     = contains(["dev", "stage", "prod"], var.environment)
    error_message = "environment must be dev, stage, or prod."
  }
}

variable "backend_image" {
  description = "Backend image URI. Leave empty during bootstrap; install.sh supplies the built image on the full apply."
  type        = string
  default     = ""
}

variable "firebase_site_id" {
  description = "Globally unique Firebase Hosting site ID."
  type        = string
}

variable "app_url" {
  description = "Final browser URL. Use the Firebase URL initially, for example https://SITE_ID.web.app."
  type        = string
}

variable "db_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-f1-micro"
}

variable "db_high_availability" {
  description = "Use regional Cloud SQL availability. Recommended for production."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Protect Cloud SQL and storage from accidental Terraform deletion."
  type        = bool
  default     = true
}

variable "min_instances" {
  type    = number
  default = 1
}

variable "max_instances" {
  type    = number
  default = 10
}

variable "gmail_user" {
  description = "Optional Google Workspace/Gmail SMTP sender."
  type        = string
  default     = ""
}

variable "gmail_app_password" {
  description = "Optional Gmail app password. This value is sensitive and will exist in Terraform state."
  type        = string
  sensitive   = true
  default     = ""
}

variable "openai_api_key" {
  description = "Optional OpenAI fallback provider key."
  type        = string
  sensitive   = true
  default     = ""
}

variable "cohere_api_key" {
  description = "Optional Cohere reranking key."
  type        = string
  sensitive   = true
  default     = ""
}

variable "stripe_secret_key" {
  description = "Optional Stripe billing secret key."
  type        = string
  sensitive   = true
  default     = ""
}

variable "stripe_webhook_secret" {
  description = "Optional Stripe DocIntel webhook secret."
  type        = string
  sensitive   = true
  default     = ""
}

variable "stripe_restaurant_webhook_secret" {
  description = "Optional restaurant workflow Stripe webhook secret."
  type        = string
  sensitive   = true
  default     = ""
}

variable "stripe_pro_price_id" {
  description = "Optional Stripe Pro price ID."
  type        = string
  default     = ""
}

variable "stripe_enterprise_price_id" {
  description = "Optional Stripe Enterprise price ID."
  type        = string
  default     = ""
}

variable "labels" {
  type = map(string)
  default = {
    application = "docintel"
    managed_by  = "terraform"
  }
}
