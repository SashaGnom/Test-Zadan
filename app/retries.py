def retry_delay(retry_number: int) -> int:
    if retry_number < 1:
        raise ValueError("retry_number must be greater than zero")
    return min(2 ** (retry_number - 1), 60)
