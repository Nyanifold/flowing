"""pytest 公共配置：fixtures 目录定位与持久化语料拷贝工具。"""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """``tests/fixtures/`` 根目录（目录即契约，见总路线图 §3）。"""
    return FIXTURES_DIR


@pytest.fixture
def copy_fixture(tmp_path):
    """把 fixtures 下的文件拷入 tmp_path 后返回副本路径。

    FileRecordStore 的压缩 / 迁移回写会原地改写文件，语料原件必须保持
     pristine，凡涉及写行为的测试一律经本 fixture 取副本。
    """

    def _copy(rel: str) -> Path:
        src = FIXTURES_DIR / rel
        dst = tmp_path / src.name
        dst.write_bytes(src.read_bytes())
        return dst

    return _copy
