"""Physical NoiseModelling parameters accepted in "custom" mode, with allowed ranges."""

# name: (type, min, max); booleans have no range
CUSTOM_PARAMS = {
    "confMaxSrcDist": (float, 10, 500),      # m; the OSM extract buffer grows to match it
    "confMaxReflDist": (float, 0, 500),      # m
    "confReflOrder": (int, 0, 3),            # each extra order costs a lot of time
    "paramWallAlpha": (float, 0, 0.99),      # wall absorption, must stay < 1
    "confDiffVertical": (bool, None, None),
    "confDiffHorizontal": (bool, None, None),
    "confHumidity": (float, 0, 100),         # %
    "confTemperature": (float, -40, 50),     # degrees C
}


def validate_params(params: dict) -> dict:
    """Return the cleaned parameters or raise ValueError with a clear message."""
    unknown = sorted(set(params) - set(CUSTOM_PARAMS))
    if unknown:
        raise ValueError(f"unknown parameters: {', '.join(unknown)}; "
                         f"allowed: {', '.join(CUSTOM_PARAMS)}")
    clean = {}
    for name, value in params.items():
        kind, low, high = CUSTOM_PARAMS[name]
        if kind is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false")
        else:
            # in Python True/False are also ints: reject them explicitly
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")
            if kind is int and value != int(value):
                raise ValueError(f"{name} must be an integer")
            value = kind(value)
            if not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}")
        clean[name] = value
    return clean