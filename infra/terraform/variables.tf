variable "client" {
  description = "Client slug. One deployment per client, usually in the client's own AWS account."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9-]{2,20}$", var.client))
    error_message = "client must be 2-20 chars of a-z, 0-9, -"
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "image_tag" {
  description = "Tag pushed to the ECR repositories by scripts/deploy.sh"
  type        = string
  default     = "latest"
}

variable "allowed_cidrs" {
  description = "Who may reach the operator console. Lock this to the client's office / VPN ranges."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS. Empty = HTTP only (demo)."
  type        = string
  default     = ""
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_deletion_protection" {
  type    = bool
  default = false
}

variable "worker_count" {
  type    = number
  default = 1
}

variable "worker_cpu" {
  type    = number
  default = 1024
}

variable "worker_memory" {
  type    = number
  default = 3072
}

variable "max_browser_sessions" {
  description = "Concurrent browser sessions per worker task"
  type        = number
  default     = 3
}

variable "grounder_chain" {
  description = "Grounding tiers in order. cache,qwen,claude needs qwen_url and anthropic_api_key."
  type        = string
  default     = "cache,dom"
}

variable "qwen_url" {
  description = "OpenAI-compatible endpoint serving the fine-tuned click policy (vLLM). Empty disables the tier."
  type        = string
  default     = ""
}

variable "anthropic_api_key" {
  description = "Stored in Secrets Manager, injected into the worker and API. Empty disables the Claude tier."
  type        = string
  default     = ""
  sensitive   = true
}

variable "deploy_mock_portal" {
  description = "Deploy the mock legacy portal (demo). A real client deployment points PORTAL_URL at their portal."
  type        = bool
  default     = true
}

variable "portal_url" {
  description = "URL of the client's real portal when deploy_mock_portal = false"
  type        = string
  default     = ""
}

variable "temporal_address" {
  description = "Temporal Cloud / external cluster address. Empty = self-host Temporal on ECS (demo-grade)."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "alarm_email" {
  description = "Optional email for CloudWatch alarms"
  type        = string
  default     = ""
}

variable "free_tier_account" {
  description = "AWS Free plan accounts reject RDS backups > 1 day, storage autoscaling and Performance Insights"
  type        = bool
  default     = false
}
