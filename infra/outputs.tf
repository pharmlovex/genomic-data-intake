output "api_url" {
  description = "Base URL of the intake API."
  value       = local.deploy_functions ? "https://${oci_apigateway_gateway.intake.hostname}/v1" : "(deploy the function images first)"
}

output "bucket" {
  value = oci_objectstorage_bucket.intake.name
}

output "namespace" {
  value = local.namespace
}

output "ocir_prefix" {
  description = "Prefix for docker tags, e.g. <ocir_prefix>/validator:0.1.0"
  value       = "${var.region}.ocir.io/${local.namespace}/${var.name}"
}

output "validator_image" {
  value = local.validator_image
}

output "s3_compat_endpoint" {
  description = "Amazon S3 Compatibility API endpoint, used by Nextflow to read inputs and publish results."
  value       = "https://${local.namespace}.compat.objectstorage.${var.region}.oraclecloud.com"
}

output "oke_cluster_id" {
  value = var.enable_oke ? oci_containerengine_cluster.pipeline[0].id : null
}
