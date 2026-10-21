from services.execution.handlers.workspace import dependency_install_hint


def test_a_missing_python_package_names_pip():
    hint = dependency_install_hint("", "ModuleNotFoundError: No module named 'pandas'")
    assert "pip install pandas" in hint


def test_a_missing_node_package_names_npm():
    hint = dependency_install_hint("", "Error: Cannot find module 'left-pad'")
    assert "npm install left-pad" in hint


def test_a_missing_command_is_named():
    hint = dependency_install_hint("", "sh: 1: ripgrep: not found")
    assert "ripgrep" in hint
    assert "not installed" in hint


def test_an_ordinary_failure_gets_no_guess():
    assert dependency_install_hint("", "AssertionError: expected 2 but got 3") == ""
