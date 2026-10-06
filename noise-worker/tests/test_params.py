import pytest

from noise_worker.params import validate_params


def test_valid_params_are_cleaned():
    clean = validate_params({"confMaxSrcDist": 300, "confDiffHorizontal": True, "confReflOrder": 2})
    assert clean == {"confMaxSrcDist": 300.0, "confDiffHorizontal": True, "confReflOrder": 2}
    assert isinstance(clean["confMaxSrcDist"], float)


@pytest.mark.parametrize("params, message", [
    ({"foo": 1}, "unknown parameters: foo"),
    ({"confReflOrder": 5}, "between 0 and 3"),
    ({"confMaxSrcDist": 5000}, "between 10 and 500"),
    ({"confReflOrder": 1.5}, "must be an integer"),
    ({"confMaxSrcDist": True}, "must be a number"),
    ({"confMaxSrcDist": "300"}, "must be a number"),
    ({"confDiffVertical": 1}, "must be true or false"),
])
def test_invalid_params_are_rejected(params, message):
    with pytest.raises(ValueError, match=message):
        validate_params(params)