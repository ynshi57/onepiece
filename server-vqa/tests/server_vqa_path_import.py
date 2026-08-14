import importlib.util
from pathlib import Path


def import_tool(name: str):
    path = Path(__file__).resolve().parents[1] / "tools" / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module
