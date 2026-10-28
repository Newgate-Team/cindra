import ast
import tomllib
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _local_packages_imported_by_the_api() -> set[str]:
    local = {p.name for p in ROOT.iterdir() if (p / "__init__.py").exists()} - {"app", "tests"}
    imported: set[str] = set()
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
    return imported & local


def test_every_local_package_the_api_imports_ships_with_it() -> None:
    # Tests run from the source tree, where every folder is importable;
    # the image only has what the Dockerfile copies and pip installs. The
    # montage package builder importing davinci_agent would have crashed
    # the API at startup in production with all tests green.
    needed = _local_packages_imported_by_the_api()
    assert "davinci_agent" in needed
    dockerfile = (ROOT / "Dockerfile").read_text()
    include = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"][
        "packages"
    ]["find"]["include"]
    for package in needed:
        assert f"COPY {package} ./{package}" in dockerfile, package
        assert any(fnmatch(package, pattern) for pattern in include), package
