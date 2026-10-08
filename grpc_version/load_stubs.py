"""Load protoc output from this directory without editing generated imports.

grpc_tools generates ``import inventory_pb2`` inside inventory_pb2_grpc.py.
Register that module in sys.modules before loading the grpc stub so imports
work when the process is started from the repository root.
"""

import importlib.util
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def _load(module_name: str):
    expected = _DIR / f"{module_name}.py"
    existing = sys.modules.get(module_name)
    if existing is not None:
        existing_file = getattr(existing, "__file__", None)
        if existing_file and Path(existing_file).resolve() == expected.resolve():
            return existing

    if not expected.is_file():
        raise ImportError(
            f"Missing {expected}. From the repository root run: "
            "python -m grpc_tools.protoc -I grpc_version "
            "--python_out=grpc_version --grpc_python_out=grpc_version "
            "grpc_version/inventory.proto"
        )

    spec = importlib.util.spec_from_file_location(module_name, expected)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load generated module {expected}")
    module = importlib.util.module_from_spec(spec)
    # Present before exec so inventory_pb2_grpc's "import inventory_pb2" hits this module.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


inventory_pb2 = _load("inventory_pb2")
inventory_pb2_grpc = _load("inventory_pb2_grpc")
