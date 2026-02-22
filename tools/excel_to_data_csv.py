# -*- coding: utf-8 -*-
"""
管理者用Excel（admin_edit...xlsx）を編集し、既存の data.csv を安全に更新するツール（チェック強化版）

重要ポリシー（初心者運用向け）
- data.csv のヘッダー（列構成・列順）は絶対に変更しない
- 管理者は「既存レコードの更新のみ」：新規追加は禁止（id等が必要で事故るため）
- 必須項目の空欄・フラグ値の不正・区切り文字ミスを検知して止める
- 更新前に必ずバックアップを作る
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd


# Excel列名（管理者シート）→ data.csv列名（更新先）
COL_MAP: Dict[str, str] = {
    "name": "name",
    "category": "category",
    "overview": "overview",
    "aliases（;区切り）": "aliases",
    "differential_points（;区切り）": "differential_points",
    "treatment_title": "treatment_title",
    "first_line_treatment": "first_line_treatment",
    "severity_flag": "severity_flag",
    "referral_flag": "referral_flag",
    "insurance_flag": "insurance_flag",
    "cost": "cost",
}

# 許容値（不正なら停止）
ALLOWED = {
    "severity_flag": {"軽症", "中等症", "重症"},
    "referral_flag": {"経過観察", "要専門医紹介", "緊急対応"},
    "insurance_flag": {"保険", "自費", "混在"},
}

# 管理者Excelで必須（空欄なら停止）
REQUIRED_EXCEL_COLS = [
    "name",
    "overview",
    "treatment_title",
    "first_line_treatment",
    "severity_flag",
    "referral_flag",
    "insurance_flag",
]

# セミコロン区切り推奨の列（初心者事故が多い）
SEMICOLON_COLUMNS = {"aliases", "differential_points"}


def _now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _clean_cell(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip()
    # Excelが数値扱いした "1.0" などを戻す
    if s.endswith(".0") and s.replace(".", "", 1).isdigit():
        s = s[:-2]
    return s


def _normalize_semicolons(s: str) -> str:
    # 全角セミコロンを半角に統一
    return s.replace("；", ";").replace("︔", ";").replace("﹔", ";")


def load_admin_excel(excel_path: Path, sheet_name: str) -> pd.DataFrame:
    if not excel_path.exists():
        raise FileNotFoundError(f"Excelが見つかりません: {excel_path}")

    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=str).fillna("")
    for c in df.columns:
        df[c] = df[c].map(_clean_cell)

    # name列がないと照合できない
    if "name" not in df.columns:
        raise ValueError("Excelに 'name' 列がありません（テンプレの1行目を変更していないか確認）")

    # nameが空の行は無視
    df["name"] = df["name"].map(_clean_cell)
    df = df[df["name"] != ""].copy()

    # 必須列が存在するか
    missing_cols = [c for c in REQUIRED_EXCEL_COLS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Excelに必須列がありません: {missing_cols}")

    # 値の整形（全角セミコロン→半角）
    # まず data側列名に寄せてから処理
    rename_dict = {c: COL_MAP.get(c, c) for c in df.columns}
    df = df.rename(columns=rename_dict)

    for col in SEMICOLON_COLUMNS:
        if col in df.columns:
            df[col] = df[col].astype(str).map(_clean_cell).map(_normalize_semicolons)

    return df


def load_data_csv(csv_path: Path) -> Tuple[pd.DataFrame, List[str]]:
    if not csv_path.exists():
        raise FileNotFoundError(f"data.csv が見つかりません: {csv_path}")
    data = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    headers = list(data.columns)
    return data, headers


def backup_csv(csv_path: Path) -> Path:
    backup = csv_path.with_name(f"{csv_path.stem}_backup_{_now_stamp()}{csv_path.suffix}")
    backup.write_bytes(csv_path.read_bytes())
    return backup


def validate_admin_rows(admin_df: pd.DataFrame) -> None:
    """
    admin_df は既に data側列名に寄せてある前提
    """
    errors: List[str] = []
    warnings: List[str] = []

    # 1) 必須の空欄チェック（行番号つき）
    # Excelの見た目行番号: ヘッダーが1行目なのでデータは2行目から
    for i, row in admin_df.reset_index(drop=True).iterrows():
        excel_row = i + 2
        for col in REQUIRED_EXCEL_COLS:
            mapped = COL_MAP.get(col, col)  # 既に寄せてるけど念のため
            val = _clean_cell(row.get(mapped, ""))
            if val == "":
                errors.append(f"Excel {excel_row}行目: '{mapped}' が空欄です（必須）")

        # 2) フラグ許容値チェック
        for col, allowed in ALLOWED.items():
            if col in admin_df.columns:
                v = _clean_cell(row.get(col, ""))
                if v == "":
                    errors.append(f"Excel {excel_row}行目: '{col}' が未選択です（必須）")
                elif v not in allowed:
                    errors.append(f"Excel {excel_row}行目: '{col}' の値 '{v}' が不正です（許可: {sorted(allowed)}）")

        # 3) 区切り文字ミス（カンマ区切り）を検知
        for col in SEMICOLON_COLUMNS:
            if col in admin_df.columns:
                s = _clean_cell(row.get(col, ""))
                if "," in s:
                    errors.append(
                        f"Excel {excel_row}行目: '{col}' にカンマ(,)が含まれています。区切りは ';' を使ってください。"
                    )

    # 4) name重複チェック（Excel内）
    names = admin_df["name"].map(_clean_cell).tolist() if "name" in admin_df.columns else []
    dup = sorted({n for n in names if n and names.count(n) > 1})
    if dup:
        errors.append(f"Excel内で 'name' が重複しています: {dup}（同じ病名が複数行にあると事故ります）")

    if warnings:
        # 今回はwarningは使わず、必要なら将来拡張
        pass

    if errors:
        raise ValueError("\n".join(errors))


def merge_update_existing_only(
    data_df: pd.DataFrame,
    admin_df: pd.DataFrame,
    headers: List[str],
    key: str = "name",
) -> Tuple[pd.DataFrame, List[str]]:
    """
    既存更新のみ（新規追加は禁止）
    """
    if key not in data_df.columns:
        raise ValueError(f"data.csv にキー列 '{key}' がありません。data.csvヘッダーを確認してください。")

    data_df = data_df.copy()
    data_df[key] = data_df[key].map(_clean_cell)

    idx = {n: i for i, n in enumerate(data_df[key].tolist()) if n != ""}

    changes: List[str] = []
    blocked_new: List[str] = []

    for i, row in admin_df.reset_index(drop=True).iterrows():
        excel_row = i + 2
        name = _clean_cell(row.get(key, ""))
        if not name:
            continue

        if name not in idx:
            blocked_new.append(f"Excel {excel_row}行目: '{name}' は data.csv に存在しません（新規追加は禁止：管理者では対応しない）")
            continue

        data_i = idx[name]
        for col, v in row.items():
            if col == key:
                continue
            if col not in data_df.columns:
                # data.csvに存在しない列は無視（ヘッダー変更しない）
                continue

            v2 = _clean_cell(v)
            if v2 == "":
                # 空欄は上書きしない（誤消し防止）
                continue

            old = _clean_cell(data_df.at[data_i, col])
            if v2 != old:
                data_df.at[data_i, col] = v2
                changes.append(f"[更新] {name} : {col} '{old}' → '{v2}'")

    if blocked_new:
        raise ValueError("\n".join(blocked_new))

    data_df = data_df.reindex(columns=headers)  # 列順維持
    return data_df, changes


def main():
    ap = argparse.ArgumentParser(description="Excel編集内容で data.csv を安全に更新します（チェック強化版）")
    ap.add_argument("--excel", default="admin_edit_template_unprotected.xlsx", help="管理者用Excelのパス")
    ap.add_argument("--sheet", default="admin_edit", help="シート名（既定：admin_edit）")
    ap.add_argument("--csv", default="data.csv", help="更新対象の data.csv のパス")
    ap.add_argument("--key", default="name", help="照合キー列（既定：name）")
    ap.add_argument("--dry-run", action="store_true", help="書き込みせず変更内容だけ表示")
    args = ap.parse_args()

    excel_path = Path(args.excel)
    csv_path = Path(args.csv)

    admin_df = load_admin_excel(excel_path, args.sheet)
    validate_admin_rows(admin_df)

    data_df, headers = load_data_csv(csv_path)

    merged, changes = merge_update_existing_only(data_df, admin_df, headers, key=args.key)

    print("=== 変更内容 ===")
    if changes:
        for c in changes:
            print(c)
    else:
        print("変更はありません。")

    if args.dry_run:
        print("\n(dry-run) 書き込みは行いません。")
        return

    bkup = backup_csv(csv_path)
    merged.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\nOK: data.csv を更新しました。バックアップ: {bkup}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nERROR:", str(e))
        print("\n対処: ERROR行に書いてある『Excel○行目』を直して、もう一度実行してください。")
        sys.exit(1)