from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd


MODELED_HP_HEATING_CAPACITY_KBTUH = 41.669
MODELED_UNIT_COUNT_BY_CLIMATE_ZONE_AND_BUILDING_TYPE = {
    ("4", "MFm"): 2.8569,
    ("6", "Hotel"): 3.7503,
    ("3", "Hotel"): 4.4012,
    ("6", "Grocery"): 1.9867,
    ("9", "Nrs"): 8.8021,
}
BUILDING_TYPE_MAP = {
    "GRO": "Grocery",
    "GROCERY": "Grocery",
    "HTL": "Hotel",
    "HOTEL": "Hotel",
    "MFM": "MFm",
    "MFm": "MFm",
    "NRS": "Nrs",
}


def normalize_column_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def find_column(columns: list[str], required_terms: list[str], optional_terms: list[str] | None = None) -> str | None:
    optional_terms = optional_terms or []
    normalized = {column: normalize_column_name(column) for column in columns}
    for column, normalized_name in normalized.items():
        if all(term in normalized_name for term in required_terms) and all(
            term in normalized_name for term in optional_terms
        ):
            return column
    return None


def normalize_climate_zone(value: object) -> str:
    text = str(value).strip().upper()
    match = re.search(r"(\d+)", text)
    if not match:
        return text
    return str(int(match.group(1)))


def normalize_building_type(value: object) -> str:
    text = str(value).strip()
    return BUILDING_TYPE_MAP.get(text.upper(), text)


def parse_number(value: object) -> float | None:
    if pd.isna(value):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    return float(match.group(0))


def read_claim_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        data = pd.read_csv(path)
    elif path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        data = pd.read_excel(path, engine="openpyxl")
    else:
        return pd.DataFrame()

    columns = list(data.columns)
    hp_count_col = find_column(columns, ["number", "heat", "pump", "water", "heaters"])
    cop_col = find_column(columns, ["efficiency"])
    storage_col = find_column(columns, ["storage", "capacity", "heat", "pump"])
    output_col = find_column(columns, ["output", "heating", "capacity", "heat", "pump"])

    rows = pd.DataFrame(
        {
            "source_file": path.name,
            "year": data.get("Year"),
            "pa": data.get("PA"),
            "claim_id": data.get("ClaimID"),
            "building_type_raw": data.get("BldgType"),
            "building_location_raw": data.get("BldgLoc"),
            "measure_application_type": data.get("MeasAppType_DC"),
            "measure_code": data.get("MeasCode"),
            "claimed_numunits_kbtuh": data.get("NumUnits").map(parse_number) if "NumUnits" in data else None,
            "hp_count": data[hp_count_col].map(parse_number) if hp_count_col else None,
            "hp_cop_or_uef": data[cop_col].map(parse_number) if cop_col else None,
            "storage_gallons_per_hp": data[storage_col].map(parse_number) if storage_col else None,
            "output_kbtuh_per_hp": data[output_col].map(parse_number) if output_col else None,
        }
    )

    rows["building_type"] = rows["building_type_raw"].map(normalize_building_type)
    rows["climate_zone"] = rows["building_location_raw"].map(normalize_climate_zone)
    rows["calculated_capacity_kbtuh"] = rows["hp_count"] * rows["output_kbtuh_per_hp"]
    rows["installed_capacity_kbtuh"] = rows["claimed_numunits_kbtuh"].fillna(rows["calculated_capacity_kbtuh"])
    rows["capacity_source"] = rows["claimed_numunits_kbtuh"].notna().map(
        {True: "NumUnits", False: "HP count x output kBtuh"}
    )
    return rows


def add_model_capacity(data: pd.DataFrame) -> pd.DataFrame:
    data = data.copy()
    data["model_unit_count"] = data.apply(
        lambda row: MODELED_UNIT_COUNT_BY_CLIMATE_ZONE_AND_BUILDING_TYPE.get(
            (str(row["climate_zone"]), row["building_type"])
        ),
        axis=1,
    )
    data["model_hp_capacity_per_unit_kbtuh"] = MODELED_HP_HEATING_CAPACITY_KBTUH
    data["model_total_capacity_kbtuh"] = data["model_unit_count"] * data["model_hp_capacity_per_unit_kbtuh"]
    data["modeled_pair_available"] = data["model_total_capacity_kbtuh"].notna()
    data["installed_minus_model_kbtuh"] = data["installed_capacity_kbtuh"] - data["model_total_capacity_kbtuh"]
    data["installed_pct_diff_vs_model"] = data["installed_minus_model_kbtuh"] / data["model_total_capacity_kbtuh"]
    data["installed_to_model_ratio"] = data["installed_capacity_kbtuh"] / data["model_total_capacity_kbtuh"]
    return data


def build_summary(detail: pd.DataFrame) -> pd.DataFrame:
    comparable = detail[detail["modeled_pair_available"] & detail["installed_capacity_kbtuh"].notna()].copy()
    return (
        comparable.groupby(
            [
                "building_type",
                "climate_zone",
                "measure_application_type",
                "hp_cop_or_uef",
                "storage_gallons_per_hp",
                "model_unit_count",
                "model_hp_capacity_per_unit_kbtuh",
                "model_total_capacity_kbtuh",
            ],
            dropna=False,
            as_index=False,
        )
        .agg(
            claim_count=("claim_id", "nunique"),
            row_count=("claim_id", "size"),
            installed_capacity_total_kbtuh=("installed_capacity_kbtuh", "sum"),
            installed_capacity_mean_kbtuh=("installed_capacity_kbtuh", "mean"),
            installed_capacity_median_kbtuh=("installed_capacity_kbtuh", "median"),
            installed_capacity_min_kbtuh=("installed_capacity_kbtuh", "min"),
            installed_capacity_max_kbtuh=("installed_capacity_kbtuh", "max"),
            hp_count_total=("hp_count", "sum"),
            output_kbtuh_per_hp_median=("output_kbtuh_per_hp", "median"),
            source_files=("source_file", lambda values: ", ".join(sorted(set(map(str, values))))),
        )
        .assign(
            median_minus_model_kbtuh=lambda df: df["installed_capacity_median_kbtuh"]
            - df["model_total_capacity_kbtuh"],
            median_pct_diff_vs_model=lambda df: df["median_minus_model_kbtuh"]
            / df["model_total_capacity_kbtuh"],
            median_installed_to_model_ratio=lambda df: df["installed_capacity_median_kbtuh"]
            / df["model_total_capacity_kbtuh"],
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare claimed installed kBtuh capacity to Avoided Costs notebook model capacity assumptions."
    )
    parser.add_argument("--claims-dir", default="claimed nunits", type=Path)
    parser.add_argument("--output-dir", default="outputs", type=Path)
    args = parser.parse_args()

    files = sorted(
        path
        for path in args.claims_dir.iterdir()
        if path.suffix.lower() in {".csv", ".xlsx", ".xlsm", ".xls"}
    )
    if not files:
        raise FileNotFoundError(f"No claim files found in {args.claims_dir}")

    detail = pd.concat([read_claim_file(path) for path in files], ignore_index=True)
    detail = add_model_capacity(detail)
    summary = build_summary(detail)

    args.output_dir.mkdir(exist_ok=True)
    detail_path = args.output_dir / "claimed_nunits_model_capacity_comparison_detail.csv"
    summary_path = args.output_dir / "claimed_nunits_model_capacity_comparison_summary.csv"
    detail.to_csv(detail_path, index=False, float_format="%.6f")
    summary.to_csv(summary_path, index=False, float_format="%.6f")

    comparable_count = int(detail["modeled_pair_available"].sum())
    print(f"Wrote {len(detail):,} detail rows to {detail_path}")
    print(f"Wrote {len(summary):,} summary rows to {summary_path}")
    print(f"Rows matching modeled CZ/building pairs: {comparable_count:,}")


if __name__ == "__main__":
    main()
