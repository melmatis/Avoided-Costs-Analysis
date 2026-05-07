from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd


MODEL_FIRST_BASELINE_COL_INDEX = 16  # Excel-style column Q in the raw CSV rows.
MODEL_SECOND_BASELINE_COL_INDEX = 17  # Excel-style column R in the raw CSV rows.


def normalize_climate_zone(value: object) -> str:
    text = str(value).strip().upper()
    match = re.search(r"(\d+)", text)
    if not match:
        return text
    return str(int(match.group(1)))


def normalize_swwh_measure_code(value: object) -> str:
    text = str(value).strip().upper()
    if re.fullmatch(r"[A-Z]", text):
        return f"SWWH028{text}"
    if re.fullmatch(r"028[A-Z]", text):
        return f"SWWH{text}"
    return text


def read_model_file(path: Path) -> pd.DataFrame:
    totals = defaultdict(lambda: {"first": 0.0, "second": 0.0, "hours": 0})

    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file)
        next(reader, None)
        for row in reader:
            if len(row) <= MODEL_SECOND_BASELINE_COL_INDEX:
                continue
            if not row or row[0] == "Sector":
                continue

            try:
                hour = int(float(row[11]))
                first_baseline = float(row[MODEL_FIRST_BASELINE_COL_INDEX])
                second_baseline = float(row[MODEL_SECOND_BASELINE_COL_INDEX])
            except (TypeError, ValueError):
                continue

            if hour < 1 or hour > 8760:
                continue

            key = (
                path.name,
                row[0].strip(),
                row[1].strip(),
                row[4].strip(),
                normalize_climate_zone(row[4]),
                row[10].strip(),
            )
            totals[key]["first"] += first_baseline
            totals[key]["second"] += second_baseline
            totals[key]["hours"] += 1

    rows = []
    for (file_name, sector, building_type, building_location, climate_zone, tech_id), values in totals.items():
        rows.append(
            {
                "model_file": file_name,
                "model_sector": sector,
                "building_type": building_type,
                "building_location": building_location,
                "climate_zone": climate_zone,
                "model_tech_id": tech_id,
                "model_hours": values["hours"],
                "model_existing_therm_per_kbtuh": values["first"],
                "model_standard_therm_per_kbtuh": values["second"],
            }
        )

    return pd.DataFrame(rows)


def build_swwh_lookup(swwh_path: Path) -> pd.DataFrame:
    swwh = pd.read_excel(swwh_path, sheet_name="SWWH028", engine="openpyxl")
    swwh = swwh.copy()
    swwh["climate_zone"] = swwh["E3ClimateZone"].map(normalize_climate_zone)
    swwh["building_type"] = swwh["BldgType"].astype(str).str.strip()
    swwh["meas_app_type"] = swwh["MeasAppType"].astype(str).str.strip().str.upper()
    swwh["meas_code"] = swwh["MeasCode"].map(normalize_swwh_measure_code)
    swwh["swwh_existing_therm_per_kbtuh"] = pd.to_numeric(
        swwh["UnitTherm1stBaseline"],
        errors="coerce",
    )
    swwh["swwh_standard_therm_per_kbtuh"] = pd.to_numeric(
        swwh["UnitTherm1stBaseline"],
        errors="coerce",
    )
    return swwh[
        [
            "building_type",
            "climate_zone",
            "meas_app_type",
            "meas_code",
            "MeasureID",
            "swwh_existing_therm_per_kbtuh",
            "swwh_standard_therm_per_kbtuh",
            "UnitTherm1stBaseline",
            "UnitTherm2ndBaseline",
        ]
    ].drop_duplicates()


def compare_model_to_swwh(model: pd.DataFrame, swwh: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail_rows = []

    for _, model_row in model.iterrows():
        for baseline, model_col, swwh_meas_app, swwh_col in [
            ("Existing", "model_existing_therm_per_kbtuh", "AR", "swwh_existing_therm_per_kbtuh"),
            ("Standard", "model_standard_therm_per_kbtuh", "NR", "swwh_standard_therm_per_kbtuh"),
        ]:
            matches = swwh[
                swwh["building_type"].eq(model_row["building_type"])
                & swwh["climate_zone"].eq(model_row["climate_zone"])
                & swwh["meas_app_type"].eq(swwh_meas_app)
            ].copy()

            if matches.empty:
                detail_rows.append(
                    {
                        **model_row.to_dict(),
                        "baseline": baseline,
                        "model_therm_per_kbtuh": model_row[model_col],
                        "swwh_meas_app_type": swwh_meas_app,
                        "swwh_meas_code": None,
                        "swwh_measure_id": None,
                        "swwh_therm_per_kbtuh": None,
                        "model_minus_swwh": None,
                        "model_pct_diff_vs_swwh": None,
                        "match_status": "no SWWH028 match for building/CZ/baseline",
                    }
                )
                continue

            for _, match in matches.iterrows():
                swwh_value = match[swwh_col]
                diff = model_row[model_col] - swwh_value if pd.notna(swwh_value) else None
                pct_diff = diff / swwh_value if pd.notna(swwh_value) and swwh_value != 0 else None
                detail_rows.append(
                    {
                        **model_row.to_dict(),
                        "baseline": baseline,
                        "model_therm_per_kbtuh": model_row[model_col],
                        "swwh_meas_app_type": swwh_meas_app,
                        "swwh_meas_code": match["meas_code"],
                        "swwh_measure_id": match["MeasureID"],
                        "swwh_therm_per_kbtuh": swwh_value,
                        "model_minus_swwh": diff,
                        "model_pct_diff_vs_swwh": pct_diff,
                        "match_status": "matched on building/CZ/baseline",
                    }
                )

    detail = pd.DataFrame(detail_rows)
    matched = detail[detail["swwh_therm_per_kbtuh"].notna()].copy()

    if matched.empty:
        summary = detail.copy()
    else:
        summary = (
            matched.groupby(
                [
                    "model_file",
                    "model_sector",
                    "building_type",
                    "building_location",
                    "climate_zone",
                    "model_tech_id",
                    "model_hours",
                    "baseline",
                    "model_therm_per_kbtuh",
                    "swwh_meas_app_type",
                ],
                dropna=False,
                as_index=False,
            )
            .agg(
                swwh_therm_per_kbtuh_median=("swwh_therm_per_kbtuh", "median"),
                swwh_therm_per_kbtuh_min=("swwh_therm_per_kbtuh", "min"),
                swwh_therm_per_kbtuh_max=("swwh_therm_per_kbtuh", "max"),
                swwh_match_count=("swwh_therm_per_kbtuh", "size"),
                swwh_meas_codes=("swwh_meas_code", lambda values: ", ".join(sorted(set(map(str, values))))),
            )
        )
        summary["model_minus_swwh_median"] = (
            summary["model_therm_per_kbtuh"] - summary["swwh_therm_per_kbtuh_median"]
        )
        summary["model_pct_diff_vs_swwh_median"] = (
            summary["model_minus_swwh_median"] / summary["swwh_therm_per_kbtuh_median"]
        )

        no_match = detail[detail["swwh_therm_per_kbtuh"].isna()].copy()
        if not no_match.empty:
            summary = pd.concat([summary, no_match], ignore_index=True, sort=False)

    return detail, summary


def rename_output_columns(data: pd.DataFrame) -> pd.DataFrame:
    return data.rename(
        columns={
            "swwh_meas_app_type": "SWWH028 MeasAppType",
            "swwh_meas_code": "SWWH028 MeasCode",
            "swwh_measure_id": "SWWH028 MeasureID",
            "swwh_therm_per_kbtuh": "SWWH028 therm/kBtuh",
            "swwh_therm_per_kbtuh_median": "SWWH028 therm/kBtuh median",
            "swwh_therm_per_kbtuh_min": "SWWH028 therm/kBtuh min",
            "swwh_therm_per_kbtuh_max": "SWWH028 therm/kBtuh max",
            "swwh_match_count": "SWWH028 match count",
            "swwh_meas_codes": "SWWH028 MeasCodes",
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare Model Runs ES therm/kBtuh columns Q/R to matching SWWH028 rows."
    )
    parser.add_argument("--model-dir", default="Model Runs ES", type=Path)
    parser.add_argument("--swwh", default="SWWH028.xlsx", type=Path)
    parser.add_argument("--output-dir", default="outputs", type=Path)
    args = parser.parse_args()

    model_files = sorted(args.model_dir.glob("*.csv"))
    if not model_files:
        raise FileNotFoundError(f"No CSV files found in {args.model_dir}")

    model = pd.concat([read_model_file(path) for path in model_files], ignore_index=True)
    swwh = build_swwh_lookup(args.swwh)
    detail, summary = compare_model_to_swwh(model, swwh)

    args.output_dir.mkdir(exist_ok=True)
    detail_path = args.output_dir / "model_runs_es_swwh028_therm_kbtuh_comparison_detail.csv"
    summary_path = args.output_dir / "model_runs_es_swwh028_therm_kbtuh_comparison_summary.csv"
    rename_output_columns(detail).to_csv(detail_path, index=False, float_format="%.6f")
    rename_output_columns(summary).to_csv(summary_path, index=False, float_format="%.6f")

    print(f"Wrote {len(detail):,} detail rows to {detail_path}")
    print(f"Wrote {len(summary):,} summary rows to {summary_path}")


if __name__ == "__main__":
    main()
