def load_config(path):
    try:
        return read_toml(path)
    except Exception:
        log.warning("failed to load %s", path)
    return None
