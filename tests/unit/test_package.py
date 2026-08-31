"""Package-level smoke tests."""


def test_package_can_be_imported() -> None:
    import de01

    assert de01.__doc__
