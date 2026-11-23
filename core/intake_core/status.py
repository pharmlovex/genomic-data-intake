"""Lifecycle states for batches and files.

Every object in the system carries one of these states, and the lab's report is
built from them. Keeping them in one place makes the state machine reviewable.
"""

from enum import Enum


class FileStatus(str, Enum):
    # File landed in Object Storage but no sample sheet exists yet for its batch.
    AWAITING_SAMPLESHEET = "AWAITING_SAMPLESHEET"
    # Listed in the sample sheet; waiting for its mate (R1/R2) to arrive.
    AWAITING_PAIR = "AWAITING_PAIR"
    # A validation job has been launched for this file's sample.
    VALIDATING = "VALIDATING"
    VALID = "VALID"
    # Failed validation; the reason is recorded and the file is moved to quarantine/.
    QUARANTINED = "QUARANTINED"
    # Arrived, but the sample sheet does not list it (an orphan file).
    UNEXPECTED = "UNEXPECTED"
    # Listed in the sample sheet but not uploaded yet.
    MISSING = "MISSING"


class BatchStatus(str, Enum):
    OPEN = "OPEN"  # created, receiving files, no sample sheet yet
    SAMPLESHEET_RECEIVED = "SAMPLESHEET_RECEIVED"
    VALIDATING = "VALIDATING"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"  # something quarantined or unexpected
    READY_FOR_ANALYSIS = "READY_FOR_ANALYSIS"  # every expected file is VALID
    ANALYSIS_RUNNING = "ANALYSIS_RUNNING"
    COMPLETE = "COMPLETE"


TERMINAL_FILE_STATES = {FileStatus.VALID, FileStatus.QUARANTINED, FileStatus.UNEXPECTED}
