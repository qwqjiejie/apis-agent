from pathlib import Path

import pytest


def pytest_collection_modifyitems(items):
    """按测试目录自动应用分层 marker。"""
    for item in items:
        parts = Path(str(item.path)).parts
        for marker in ("unit", "contract", "integration", "e2e"):
            if marker in parts:
                item.add_marker(getattr(pytest.mark, marker))
                break
