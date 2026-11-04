terraform {
  required_version = ">= 1.9"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.71" }
    random = { source = "hashicorp/random", version = "~> 3.9" }
  }
  # Remote state for real client deployments (one state per client):
  # backend "s3" {
  #   bucket       = "<your-tf-state-bucket>"
  #   key          = "portalpilot/<client>/terraform.tfstate"
  #   region       = "us-east-1"
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "portalpilot"
      Client    = var.client
      ManagedBy = "terraform"
    }
  }
}

data "aws_availability_zones" "available" { state = "available" }
data "aws_caller_identity" "me" {}

locals {
  name = "pp-${var.client}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
  ns   = "${var.client}.portalpilot.local"
}
