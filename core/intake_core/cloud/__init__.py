"""OCI adapters: the only part of the code base that talks to Oracle Cloud.

Everything authenticates with resource principals, so no API keys are stored
in functions or containers. Configuration comes from environment variables,
which OCI Functions also populates from the function's configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    compartment_id: str
    namespace: str
    bucket: str
    batches_table: str
    files_table: str
    topic_id: str
    # Validation job settings (used by the launcher)
    validator_image: str
    job_subnet_id: str
    availability_domain: str
    job_shape: str
    job_ocpus: float
    job_memory_gbs: float
    par_ttl_hours: int


@lru_cache
def settings() -> Settings:
    env = os.environ.get
    return Settings(
        compartment_id=env("COMPARTMENT_ID", ""),
        namespace=env("OS_NAMESPACE", ""),
        bucket=env("BUCKET", ""),
        batches_table=env("BATCHES_TABLE", "intake_batches"),
        files_table=env("FILES_TABLE", "intake_files"),
        topic_id=env("TOPIC_ID", ""),
        validator_image=env("VALIDATOR_IMAGE", ""),
        job_subnet_id=env("JOB_SUBNET_ID", ""),
        availability_domain=env("AVAILABILITY_DOMAIN", ""),
        job_shape=env("JOB_SHAPE", "CI.Standard.E4.Flex"),
        job_ocpus=float(env("JOB_OCPUS", "2")),
        job_memory_gbs=float(env("JOB_MEMORY_GBS", "4")),
        par_ttl_hours=int(env("PAR_TTL_HOURS", "24")),
    )


@lru_cache
def signer():
    import oci

    return oci.auth.signers.get_resource_principals_signer()


def client(cls):
    """Build an OCI SDK client authenticated with the resource principal."""
    return cls(config={}, signer=signer())
