"""Launch one validation job per sample on OCI Container Instances.

Why not validate inside the function? Streaming and checksumming ~100 GB of
gzipped FASTQ runs at very roughly 100-300 MB/s per core, i.e. 5-15+ minutes
per file, well past what a function is meant for. Functions only orchestrate;
the heavy work runs in short-lived containers, one per sample, in parallel.
A batch of 50 samples means 50 containers, which exit when done.
"""

from __future__ import annotations

from . import client, settings


def job_name(batch_id: str, sample_id: str) -> str:
    return f"validate-{batch_id[:8]}-{sample_id}"[:255]


def launch_validation(batch_id: str, sample_id: str) -> str:
    import oci

    s = settings()
    m = oci.container_instances.models
    ci = client(oci.container_instances.ContainerInstanceClient)
    details = m.CreateContainerInstanceDetails(
        display_name=job_name(batch_id, sample_id),
        compartment_id=s.compartment_id,
        availability_domain=s.availability_domain,
        shape=s.job_shape,
        shape_config=m.CreateContainerInstanceShapeConfigDetails(ocpus=s.job_ocpus, memory_in_gbs=s.job_memory_gbs),
        container_restart_policy="NEVER",
        vnics=[m.CreateContainerVnicDetails(subnet_id=s.job_subnet_id, is_public_ip_assigned=False)],
        containers=[
            m.CreateContainerDetails(
                display_name="validator",
                image_url=s.validator_image,
                arguments=["--batch-id", batch_id, "--sample-id", sample_id],
                environment_variables={
                    "COMPARTMENT_ID": s.compartment_id,
                    "OS_NAMESPACE": s.namespace,
                    "BUCKET": s.bucket,
                    "BATCHES_TABLE": s.batches_table,
                    "FILES_TABLE": s.files_table,
                    "TOPIC_ID": s.topic_id,
                },
            )
        ],
        freeform_tags={"app": "genome-intake", "batch_id": batch_id, "sample_id": sample_id},
    )
    return ci.create_container_instance(details).data.id


def delete_finished_jobs(batch_id: str) -> int:
    """Delete this batch's container instances once their container has exited.

    Run at the end of each validation, so finished jobs don't linger.
    """
    import oci

    s = settings()
    ci = client(oci.container_instances.ContainerInstanceClient)
    items = oci.pagination.list_call_get_all_results(
        ci.list_container_instances, s.compartment_id, lifecycle_state="INACTIVE"
    ).data
    deleted = 0
    for item in items:
        if (item.freeform_tags or {}).get("batch_id") == batch_id:
            ci.delete_container_instance(item.id)
            deleted += 1
    return deleted
