"""Bounded artifact capacity and size-aware deadlines for full native exports."""

MAX_ARTIFACT_BYTES = 64 * 1024**3


def transfer_seconds(size):
    # Allow an 8 MiB/s transfer plus setup, retaining the former ten-minute floor.
    # This is an execution bound, not a throughput or provider performance claim.
    return min(4 * 60 * 60, max(600, (size + 8 * 1024**2 - 1) // (8 * 1024**2) + 60))
