import pytest

import pair_scanner


def test_package_has_a_version():
    assert pair_scanner.__version__


@pytest.mark.parametrize(
    "pairs,strategy,expected",
    [
        (2, 0, {"pairs": "live", "strategy": "off"}),
        (1, 1, {"pairs": "watch", "strategy": "watch"}),
    ],
)
def test_scanner_modes_says_what_each_monitor_is_doing(pairs, strategy, expected):
    from pair_scanner import scanner_modes
    from pair_scanner.settings import OPTIONS

    OPTIONS.update(pairs_mode=pairs, strategy_mode=strategy)

    assert scanner_modes() == expected


def test_public_functions_are_documented_and_typed():
    import inspect

    import pair_scanner

    for name in pair_scanner.__all__:
        if name == "__version__":
            continue
        func = getattr(pair_scanner, name)
        assert inspect.getdoc(func), name
        assert (
            inspect.signature(func).return_annotation is not inspect.Signature.empty
        ), name
