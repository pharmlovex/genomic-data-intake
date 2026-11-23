locals {
  deploy_functions = var.intake_api_image != "" && var.on_object_created_image != ""
  validator_image  = "${var.region}.ocir.io/${local.namespace}/${oci_artifacts_container_repository.validator.display_name}:${var.validator_image_tag}"
}

resource "oci_functions_application" "intake" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.name}-app"
  subnet_ids     = [oci_core_subnet.private.id]
  shape          = "GENERIC_X86"

  # Exposed to every function as environment variables.
  config = {
    COMPARTMENT_ID      = var.compartment_ocid
    OS_NAMESPACE        = local.namespace
    BUCKET              = oci_objectstorage_bucket.intake.name
    BATCHES_TABLE       = oci_nosql_table.batches.name
    FILES_TABLE         = oci_nosql_table.files.name
    TOPIC_ID            = oci_ons_notification_topic.batches.id
    VALIDATOR_IMAGE     = local.validator_image
    JOB_SUBNET_ID       = oci_core_subnet.private.id
    AVAILABILITY_DOMAIN = local.ad
    JOB_SHAPE           = var.job_shape
    JOB_OCPUS           = tostring(var.job_ocpus)
    JOB_MEMORY_GBS      = tostring(var.job_memory_gbs)
    INTAKE_API_KEY      = var.api_key
  }
}

resource "oci_functions_function" "intake_api" {
  count          = local.deploy_functions ? 1 : 0
  application_id = oci_functions_application.intake.id
  display_name   = "intake-api"
  source_details {
    source_type = "CONTAINER_IMAGE"
    image       = var.intake_api_image
  }
  memory_in_mbs      = 256
  timeout_in_seconds = 60
}

resource "oci_functions_function" "on_object_created" {
  count          = local.deploy_functions ? 1 : 0
  application_id = oci_functions_application.intake.id
  display_name   = "on-object-created"
  source_details {
    source_type = "CONTAINER_IMAGE"
    image       = var.on_object_created_image
  }
  memory_in_mbs      = 256
  timeout_in_seconds = 60
}

# Object created in the intake bucket -> on-object-created.
# The function ignores everything outside raw/ (reports, quarantine, results).
resource "oci_events_rule" "object_created" {
  count          = local.deploy_functions ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.name}-object-created"
  description    = "New upload in the intake bucket"
  is_enabled     = true
  condition = jsonencode({
    eventType = ["com.oraclecloud.objectstorage.createobject"]
    data = {
      additionalDetails = { bucketName = [oci_objectstorage_bucket.intake.name] }
    }
  })
  actions {
    actions {
      action_type = "FAAS"
      is_enabled  = true
      function_id = oci_functions_function.on_object_created[0].id
    }
  }
}

# ----- API Gateway -----

resource "oci_apigateway_gateway" "intake" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.name}-gw"
  endpoint_type  = "PUBLIC"
  subnet_id      = oci_core_subnet.public.id
}

locals {
  routes = {
    "/batches"                        = ["POST"]
    "/batches/{batch_id}"             = ["GET"]
    "/batches/{batch_id}/samplesheet" = ["PUT"]
    "/batches/{batch_id}/uploads"     = ["POST"]
  }
}

resource "oci_apigateway_deployment" "v1" {
  count          = local.deploy_functions ? 1 : 0
  compartment_id = var.compartment_ocid
  gateway_id     = oci_apigateway_gateway.intake.id
  display_name   = "${var.name}-v1"
  path_prefix    = "/v1"

  specification {
    request_policies {
      rate_limiting {
        rate_in_requests_per_second = 10
        rate_key                    = "CLIENT_IP"
      }
    }

    dynamic "routes" {
      for_each = local.routes
      content {
        path    = routes.key
        methods = routes.value
        backend {
          type        = "ORACLE_FUNCTIONS_BACKEND"
          function_id = oci_functions_function.intake_api[0].id
        }
      }
    }
  }
}
