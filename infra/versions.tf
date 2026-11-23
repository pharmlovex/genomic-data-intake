terraform {
  # 1.12 added the native "oci" state backend.
  required_version = ">= 1.12"

  # State lives in a private, versioned Object Storage bucket created outside this
  # stack (see backend.hcl.example). Namespace and region come from backend.hcl:
  #   terraform init -backend-config=backend.hcl
  backend "oci" {
    bucket              = "genome-intake-tfstate"
    key                 = "genome-intake/terraform.tfstate"
    config_file_profile = "DEFAULT"
  }

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = ">= 7.0, < 10.0"
    }
  }
}

# Auth comes from ~/.oci/config (run `oci setup config` once).
provider "oci" {
  region              = var.region
  config_file_profile = var.oci_profile
}

# Dynamic groups and tenancy-wide policies must be created in the home region.
provider "oci" {
  alias               = "home"
  region              = var.home_region != "" ? var.home_region : var.region
  config_file_profile = var.oci_profile
}
