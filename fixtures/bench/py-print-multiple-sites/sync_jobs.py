"""Job runner for the nightly directory sync."""

def sync_pair(source, target):
    print("syncing", source)
    copied = target.write_from(source)
    print("copied", copied)
    print("done")
    return copied
