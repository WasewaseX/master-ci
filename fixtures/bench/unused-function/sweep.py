"""Docket sweep entry points (fixture companion: live helpers)."""


def stamp(docket):
    return f"stamped {docket}"


def sweep(dockets):
    return [stamp(docket) for docket in dockets]


if __name__ == "__main__":
    sweep([])
