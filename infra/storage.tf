data "oci_objectstorage_namespace" "ns" {
  compartment_id = var.tenancy_ocid
}

locals {
  namespace = data.oci_objectstorage_namespace.ns.namespace
}

resource "oci_objectstorage_bucket" "intake" {
  compartment_id        = var.compartment_ocid
  namespace             = local.namespace
  name                  = "${var.name}-data"
  access_type           = "NoPublicAccess"
  object_events_enabled = true # emits com.oraclecloud.objectstorage.createobject
  versioning            = "Disabled"
  # Encrypted at rest with Oracle-managed keys by default; set kms_key_id for customer-managed keys.
}

resource "oci_objectstorage_object_lifecycle_policy" "intake" {
  namespace = local.namespace
  bucket    = oci_objectstorage_bucket.intake.name

  rules {
    name        = "archive-raw-and-results"
    action      = "ARCHIVE"
    is_enabled  = true
    target      = "objects"
    time_amount = var.archive_after_days
    time_unit   = "DAYS"
    object_name_filter {
      inclusion_prefixes = ["raw/", "results/"]
    }
  }

  rules {
    name        = "expire-quarantine"
    action      = "DELETE"
    is_enabled  = true
    target      = "objects"
    time_amount = var.quarantine_retention_days
    time_unit   = "DAYS"
    object_name_filter {
      inclusion_prefixes = ["quarantine/"]
    }
  }

  rules {
    name        = "abort-stale-multipart-uploads"
    action      = "ABORT"
    is_enabled  = true
    target      = "multipart-uploads"
    time_amount = 7
    time_unit   = "DAYS"
  }

  depends_on = [oci_identity_policy.objectstorage_lifecycle]
}

# Provisioned tables with small limits. On-demand tables reserve 10,000 read units each,
# above this tenancy's 2,000-unit NoSQL limit. Units can be raised in place later.
# Always Free (auto-reclaimable) tables only exist in a few regions, not uk-london-1,
# and genomic metadata should stay in the UK region alongside the data.
resource "oci_nosql_table" "batches" {
  compartment_id      = var.compartment_ocid
  name                = "intake_batches"
  is_auto_reclaimable = false
  ddl_statement       = <<-SQL
    CREATE TABLE IF NOT EXISTS intake_batches (
      batch_id STRING, status STRING, lab_email STRING, samplesheet_csv STRING,
      created_at STRING, updated_at STRING,
      PRIMARY KEY (batch_id))
  SQL
  table_limits {
    capacity_mode      = "PROVISIONED"
    max_read_units     = 20
    max_write_units    = 20
    max_storage_in_gbs = 1
  }
}

resource "oci_nosql_table" "files" {
  compartment_id      = var.compartment_ocid
  name                = "intake_files"
  is_auto_reclaimable = false
  ddl_statement       = <<-SQL
    CREATE TABLE IF NOT EXISTS intake_files (
      batch_id STRING, filename STRING, status STRING, sample_id STRING, reason STRING,
      md5 STRING, read_count LONG, updated_at STRING,
      PRIMARY KEY (SHARD(batch_id), filename))
  SQL
  table_limits {
    capacity_mode      = "PROVISIONED"
    max_read_units     = 20
    max_write_units    = 20
    max_storage_in_gbs = 1
  }
}

resource "oci_ons_notification_topic" "batches" {
  compartment_id = var.compartment_ocid
  name           = "${var.name}-batches"
  description    = "Batch status reports for the lab"
}

resource "oci_ons_subscription" "lab" {
  compartment_id = var.compartment_ocid
  topic_id       = oci_ons_notification_topic.batches.id
  protocol       = "EMAIL"
  endpoint       = var.lab_email
}

# ----- container registry -----

resource "oci_artifacts_container_repository" "validator" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.name}/validator"
  # Public so Container Instances can pull without an image-pull secret.
  # The image holds only open-source code; switch to private + Vault secret if that changes.
  is_public = true
}

resource "oci_artifacts_container_repository" "functions" {
  for_each       = toset(["intake-api", "on-object-created"])
  compartment_id = var.compartment_ocid
  display_name   = "${var.name}/${each.key}"
  is_public      = false
}
