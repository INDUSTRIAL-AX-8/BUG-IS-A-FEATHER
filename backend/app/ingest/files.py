"""CSV/Excel 읽기. 모든 값을 문자열로 읽고 형식 변환은 validate 에서 한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

ENCODINGS = ("utf-8-sig", "cp949", "euc-kr")


def read_table(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(path, dtype=str, keep_default_na=False)
    elif suffix in (".csv", ".txt"):
        df = None
        for enc in ENCODINGS:
            try:
                df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=enc, sep=None, engine="python")
                break
            except UnicodeDecodeError:
                continue
        if df is None:
            raise ValueError("파일 인코딩을 읽을 수 없습니다. UTF-8 또는 CP949(EUC-KR) CSV 로 저장해 주세요.")
    else:
        raise ValueError("CSV(.csv) 또는 Excel(.xlsx) 파일만 올릴 수 있습니다.")
    df.columns = [str(c).strip() for c in df.columns]
    if len(set(df.columns)) != len(df.columns):
        raise ValueError("열 이름이 중복되어 있습니다. 열 이름을 고유하게 바꿔 주세요.")
    return list(df.columns), df.to_dict("records")


def apply_mapping(rows: list[dict[str, Any]], mapping: dict[str, str]) -> list[dict[str, Any]]:
    """{시스템 필드: 원본 열} 매핑을 적용해 {시스템 필드: 값} 행으로 바꾼다."""
    return [{field: row.get(col) for field, col in mapping.items() if col} for row in rows]
