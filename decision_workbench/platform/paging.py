"""Shared integer bounds before pagination reaches SQL or in-memory slices."""

MAX_OFFSET = 2**31 - 1


def validate_offset(offset):
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ValueError("Page offset must be an integer in [0,2147483647]")


def validate_page(limit, offset, maximum=500):
    validate_offset(offset)
    if type(limit) is not int or not 1 <= limit <= maximum:
        raise ValueError(f"Page size must be an integer in [1,{maximum}]")
