terraform {
  required_version = ">= 1.6"
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
