# Service logs, viewable under Observability & Management > Logging > Log groups,
# or with: oci logging-search search-logs (see README).
# OCI keeps no function output unless a log like this exists.

resource "oci_logging_log_group" "intake" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.name}-logs"
  description    = "Genome intake functions and API Gateway"
}

# stdout/stderr and tracebacks from every function in the application.
resource "oci_logging_log" "functions" {
  display_name       = "${var.name}-functions"
  log_group_id       = oci_logging_log_group.intake.id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = var.log_retention_days

  configuration {
    compartment_id = var.compartment_ocid
    source {
      source_type = "OCISERVICE"
      service     = "functions"
      category    = "invoke"
      resource    = oci_functions_application.intake.id
    }
  }
}

# One line per API request: route, status code, latency, client IP.
# "access" = per-request summary, "execution" = gateway-side errors (auth, backend timeouts).
resource "oci_logging_log" "api" {
  for_each           = local.deploy_functions ? toset(["access", "execution"]) : toset([])
  display_name       = "${var.name}-api-${each.key}"
  log_group_id       = oci_logging_log_group.intake.id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = var.log_retention_days

  configuration {
    compartment_id = var.compartment_ocid
    source {
      source_type = "OCISERVICE"
      service     = "apigateway"
      category    = each.key
      resource    = oci_apigateway_deployment.v1[0].id
    }
  }
}
