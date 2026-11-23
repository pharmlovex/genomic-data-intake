# Monthly spend guard for the whole compartment. Budgets live in the root compartment
# and target a child compartment; the amount is in the tenancy's billing currency.

locals {
  alert_email = var.alert_email != "" ? var.alert_email : var.lab_email
}

resource "oci_budget_budget" "intake" {
  provider               = oci.home
  compartment_id         = var.tenancy_ocid
  display_name           = "${var.name}-monthly"
  description            = "Monthly spend for the genome intake compartment"
  amount                 = var.monthly_budget
  reset_period           = "MONTHLY"
  target_type            = "COMPARTMENT"
  targets                = [var.compartment_ocid]
  processing_period_type = "MONTH"
}

# Money actually spent this month has passed 80% of the budget.
resource "oci_budget_alert_rule" "actual_80" {
  provider       = oci.home
  budget_id      = oci_budget_budget.intake.id
  display_name   = "actual-80-percent"
  type           = "ACTUAL"
  threshold_type = "PERCENTAGE"
  threshold      = 80
  recipients     = local.alert_email
  message        = "Genome intake has spent 80% of its monthly budget. Check for running OKE nodes or validation containers."
}

# OCI forecasts the month will end over budget: the early warning.
resource "oci_budget_alert_rule" "forecast_100" {
  provider       = oci.home
  budget_id      = oci_budget_budget.intake.id
  display_name   = "forecast-100-percent"
  type           = "FORECAST"
  threshold_type = "PERCENTAGE"
  threshold      = 100
  recipients     = local.alert_email
  message        = "Genome intake is forecast to exceed its monthly budget. Scale OKE to 0 (oke_node_count = 0) if it is idle."
}
