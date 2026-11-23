# Optional: OKE cluster for the Nextflow variant-calling pipeline.
# A BASIC cluster has no control-plane fee; you pay for worker nodes while they run.
# Scale with oke_node_count (0 between runs) or destroy with enable_oke = false.

data "oci_containerengine_cluster_option" "all" {
  count             = var.enable_oke ? 1 : 0
  cluster_option_id = "all"
}

locals {
  k8s_version = var.enable_oke ? element(data.oci_containerengine_cluster_option.all[0].kubernetes_versions, length(data.oci_containerengine_cluster_option.all[0].kubernetes_versions) - 1) : ""
}

resource "oci_containerengine_cluster" "pipeline" {
  count              = var.enable_oke ? 1 : 0
  compartment_id     = var.compartment_ocid
  name               = "${var.name}-oke"
  kubernetes_version = local.k8s_version
  vcn_id             = oci_core_vcn.main.id
  type               = "BASIC_CLUSTER"

  endpoint_config {
    is_public_ip_enabled = true
    subnet_id            = oci_core_subnet.public.id
  }

  options {
    service_lb_subnet_ids = [oci_core_subnet.public.id]
  }
}

data "oci_containerengine_node_pool_option" "pool" {
  count               = var.enable_oke ? 1 : 0
  node_pool_option_id = oci_containerengine_cluster.pipeline[0].id
  compartment_id      = var.compartment_ocid
}

locals {
  # Newest Oracle Linux x86 OKE image that matches the cluster's Kubernetes version.
  oke_images = var.enable_oke ? [
    for s in data.oci_containerengine_node_pool_option.pool[0].sources : s.image_id
    if strcontains(s.source_name, "OKE-${trimprefix(local.k8s_version, "v")}") &&
    !strcontains(s.source_name, "aarch64") && !strcontains(s.source_name, "GPU")
  ] : []
}

resource "oci_containerengine_node_pool" "pipeline" {
  count              = var.enable_oke ? 1 : 0
  cluster_id         = oci_containerengine_cluster.pipeline[0].id
  compartment_id     = var.compartment_ocid
  name               = "${var.name}-workers"
  kubernetes_version = local.k8s_version
  node_shape         = var.oke_node_shape

  node_shape_config {
    ocpus         = var.oke_node_ocpus
    memory_in_gbs = var.oke_node_memory_gbs
  }

  node_source_details {
    source_type             = "IMAGE"
    image_id                = local.oke_images[0]
    boot_volume_size_in_gbs = 100
  }

  node_config_details {
    size = var.oke_node_count
    placement_configs {
      availability_domain = local.ad
      subnet_id           = oci_core_subnet.private.id

      dynamic "preemptible_node_config" {
        for_each = var.oke_preemptible ? [1] : []
        content {
          preemption_action {
            type                    = "TERMINATE"
            is_preserve_boot_volume = false
          }
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = length(local.oke_images) > 0
      error_message = "No x86 OKE node image found for ${local.k8s_version}."
    }
  }
}
