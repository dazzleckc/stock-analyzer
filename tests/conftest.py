"""pytest 共享 fixture"""

import polars as pl
import pytest


@pytest.fixture
def sample_stocks_old() -> pl.DataFrame:
    """构造旧的 stocks DataFrame（2 条记录）"""
    return pl.DataFrame({
        "code": ["000001", "000002"],
        "name": ["平安银行", "万科A"],
        "list_status": ["L", "L"],
        "delist_date": [None, None],
    })


@pytest.fixture
def sample_stocks_new() -> pl.DataFrame:
    """构造新的 stocks DataFrame（1 条新增、1 条改名）"""
    return pl.DataFrame({
        "code": ["000001", "600000"],
        "name": ["平安银行改名了", "浦发银行"],
        "list_status": ["L", "L"],
        "delist_date": [None, None],
    })
