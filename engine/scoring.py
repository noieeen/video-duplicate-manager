"""Quality score and keeper selection for duplicate groups."""


def score(meta: dict) -> float:
    return float(meta["width"] * meta["height"] * max(meta["bit_rate"], 1))


def pick_keeper(members: list[dict]) -> int:
    """Index (into members) of the best file: highest score, then largest size,
    then shortest path, then path ascending."""
    def key(i: int):
        m = members[i]
        return (-m["score"], -m["size"], len(m["path"]), m["path"])

    return min(range(len(members)), key=key)
