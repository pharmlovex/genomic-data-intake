variable "tenancy_ocid" {
  description = "OCID of your tenancy (from ~/.oci/config)."
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment to deploy into. Create a dedicated one, e.g. 'genome-intake'."
  type        = string
}

variable "region" {
  description = "Region to deploy into, e.g. uk-london-1."
  type        = string
  default     = "uk-london-1"
}

variable "home_region" {
  description = "Tenancy home region, for IAM resources. Leave empty if it is the same as region."
  type        = string
  default     = ""
}

variable "oci_profile" {
  description = "Profile name in ~/.oci/config."
  type        = string
  default     = "DEFAULT"
}

variable "name" {
  description = "Prefix for every resource name."
  type        = string
  default     = "genome-intake"
}

variable "lab_email" {
  description = "E-mail address subscribed to batch notifications (confirm the subscription e-mail)."
  type        = string
}

# ----- container images (pushed by scripts/deploy.sh before apply) -----

variable "intake_api_image" {
  description = "OCIR image for the intake-api function. Leave empty on the first apply."
  type        = string
  default     = ""
}

variable "on_object_created_image" {
  description = "OCIR image for the on-object-created function. Leave empty on the first apply."
  type        = string
  default     = ""
}

variable "validator_image_tag" {
  description = "Tag of the validator image in the genome-intake/validator repository."
  type        = string
  default     = "0.1.0"
}

variable "api_key" {
  description = "Shared key clients send as X-Api-Key. For a real deployment use OCI Vault and an authorizer."
  type        = string
  sensitive   = true
}

# ----- validation jobs -----

variable "job_shape" {
  description = "Container Instance shape for validation jobs."
  type        = string
  default     = "CI.Standard.E4.Flex"
}

variable "job_ocpus" {
  type    = number
  default = 1
}

variable "job_memory_gbs" {
  type    = number
  default = 4
}

# ----- data lifecycle -----

variable "archive_after_days" {
  description = "Move raw/ and results/ objects to Archive storage after this many days."
  type        = number
  default     = 90
}

variable "quarantine_retention_days" {
  description = "Delete quarantined files after this many days."
  type        = number
  default     = 30
}

# ----- optional OKE cluster for the Nextflow pipeline (costs money while running) -----

variable "enable_oke" {
  description = "Create an OKE cluster and node pool for the variant-calling pipeline."
  type        = bool
  default     = false
}

variable "oke_node_shape" {
  description = "x86 shape: bioinformatics containers are mostly amd64-only."
  type        = string
  default     = "VM.Standard.E4.Flex"
}

variable "oke_node_ocpus" {
  type    = number
  default = 2
}

variable "oke_node_memory_gbs" {
  type    = number
  default = 16
}

variable "oke_node_count" {
  description = "Set to 0 to stop paying for compute between runs."
  type        = number
  default     = 1
}

variable "oke_preemptible" {
  description = "Use preemptible nodes (much cheaper, can be reclaimed; Nextflow retries tasks)."
  type        = bool
  default     = true
}

# ----- observability and cost -----

variable "log_retention_days" {
  description = "Days to keep function and API Gateway logs (30, 60, 90, ... 180)."
  type        = number
  default     = 30
}
