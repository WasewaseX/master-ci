def load_config(path):
    try:
        return read_toml(path)
    except Exception:  # noqa: BLE001 - the boundary must fail closed
        log.warning("failed to load %s", path)
    return None
