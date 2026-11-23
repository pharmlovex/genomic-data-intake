# Least-privilege identities: functions and validation containers authenticate as
# resource principals (no API keys), and each gets only what its job needs.

resource "oci_identity_dynamic_group" "functions" {
  provider       = oci.home
  compartment_id = var.tenancy_ocid
  name           = "${var.name}-functions"
  description    = "Genome intake functions"
  matching_rule  = "ALL {resource.type = 'fnfunc', resource.compartment.id = '${var.compartment_ocid}'}"
}

resource "oci_identity_dynamic_group" "jobs" {
  provider       = oci.home
  compartment_id = var.tenancy_ocid
  name           = "${var.name}-validation-jobs"
  description    = "Genome intake validation containers"
  matching_rule  = "ALL {resource.type = 'computecontainerinstance', resource.compartment.id = '${var.compartment_ocid}'}"
}

locals {
  bucket_cond = "where target.bucket.name = '${oci_objectstorage_bucket.intake.name}'"
  comp        = "compartment id ${var.compartment_ocid}"
}

resource "oci_identity_policy" "app" {
  provider       = oci.home
  compartment_id = var.compartment_ocid
  name           = "${var.name}-app"
  description    = "Genome intake: functions and validation jobs"
  statements = [
    # Functions: issue upload PARs, write reports, track state, launch validation jobs, notify.
    "Allow dynamic-group ${oci_identity_dynamic_group.functions.name} to manage object-family in ${local.comp} ${local.bucket_cond}",
    "Allow dynamic-group ${oci_identity_dynamic_group.functions.name} to manage nosql-rows in ${local.comp}",
    "Allow dynamic-group ${oci_identity_dynamic_group.functions.name} to read nosql-tables in ${local.comp}",
    "Allow dynamic-group ${oci_identity_dynamic_group.functions.name} to manage compute-container-family in ${local.comp}",
    "Allow dynamic-group ${oci_identity_dynamic_group.functions.name} to use virtual-network-family in ${local.comp}",
    "Allow dynamic-group ${oci_identity_dynamic_group.functions.name} to use ons-topics in ${local.comp}",

    # Validation jobs: read raw data, move bad files to quarantine, write state and reports, clean up.
    "Allow dynamic-group ${oci_identity_dynamic_group.jobs.name} to manage objects in ${local.comp} ${local.bucket_cond}",
    "Allow dynamic-group ${oci_identity_dynamic_group.jobs.name} to read buckets in ${local.comp}",
    "Allow dynamic-group ${oci_identity_dynamic_group.jobs.name} to manage nosql-rows in ${local.comp}",
    "Allow dynamic-group ${oci_identity_dynamic_group.jobs.name} to read nosql-tables in ${local.comp}",
    "Allow dynamic-group ${oci_identity_dynamic_group.jobs.name} to use ons-topics in ${local.comp}",
    "Allow dynamic-group ${oci_identity_dynamic_group.jobs.name} to manage compute-container-family in ${local.comp}",

    # Platform services.
    "Allow any-user to use functions-family in ${local.comp} where ALL {request.principal.type = 'ApiGateway', request.resource.compartment.id = '${var.compartment_ocid}'}",
    "Allow service faas to read repos in ${local.comp}",
    "Allow service faas to use virtual-network-family in ${local.comp}",
    "Allow service cloudEvents to use functions-family in ${local.comp}",
  ]
}

# Object lifecycle rules run as the Object Storage service.
resource "oci_identity_policy" "objectstorage_lifecycle" {
  provider       = oci.home
  compartment_id = var.compartment_ocid
  name           = "${var.name}-lifecycle"
  description    = "Lets Object Storage archive and delete objects per the lifecycle policy"
  statements = [
    "Allow service objectstorage-${var.region} to manage object-family in ${local.comp}",
  ]
}
