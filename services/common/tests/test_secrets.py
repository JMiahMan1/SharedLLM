from services.common.secrets import secret_matches


def test_matches_only_the_exact_secret():
    assert secret_matches("s3cret-value", "s3cret-value")
    assert not secret_matches("s3cret-valuE", "s3cret-value")


def test_an_odd_header_is_refused_not_an_error():
    # str compare_digest raised TypeError on non-ASCII, turning a 401 into a 500.
    assert not secret_matches("sécret", "secret")


def test_empty_never_matches_even_an_empty_secret():
    assert not secret_matches("", "")
    assert not secret_matches(None, "x")
    assert not secret_matches("x", None)
