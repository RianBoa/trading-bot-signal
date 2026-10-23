import ast
from pathlib import Path


def _blacklist_symbols() -> set[str]:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "core"
        / "trading_core.py"
    )
    module = ast.parse(source_path.read_text(encoding="utf-8"))

    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name)
            and target.id == "BLACKLIST_SYMBOLS"
            for target in node.targets
        ):
            return set(ast.literal_eval(node.value))

    raise AssertionError("BLACKLIST_SYMBOLS is not defined")


def test_excluded_symbols_and_xaut_is_allowed():
    blacklist = _blacklist_symbols()

    assert "XAUUSDT" in blacklist
    assert "BANKUSDT" in blacklist
    assert "XAUTUSDT" not in blacklist
