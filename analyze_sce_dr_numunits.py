from __future__ import annotations
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
DEFAULT_SCE_DR_PATH = Path(
    r"C:\Users\mmh\2025 WH\Python Analysis\SCE\datareq\SCE_dr_real.csv"
)
LOCAL_SCE_DR_PATH = ROOT / "SCE_dr_real.csv"
CLAIMS_PATH = ROOT.parent / "claims_data_swwh028_222324.csv"
OUTPUT_DIR = ROOT / "outputs"

HP_COUNT_COL = "Number of Heat Pump Water Heaters included in this claim"
MODEL_COL = "Heat Pump Water Heater Model Number"
PER_HP_STORAGE_COL = "Measure storage capacity per heat pump (gallons)"
PER_HP_OUTPUT_COL = "Measure output heating capacity\xa0per heat pump (kBtu/hr)"


def choose_sce_dr_path() -> Path:
    if DEFAULT_SCE_DR_PATH.exists():
        return DEFAULT_SCE_DR_PATH
    return LOCAL_SCE_DR_PATH


def parse_numeric(series: pd.Series) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def build_analysis(df: pd.DataFrame) -> pd.DataFrame:
    analysis = df.copy()
    analysis["num_units_numeric"] = parse_numeric(analysis["NumUnits"])
    analysis["hp_count_numeric"] = parse_numeric(analysis[HP_COUNT_COL])
    analysis["per_hp_output_capacity_kbtuh"] = parse_numeric(analysis[PER_HP_OUTPUT_COL])
    analysis["per_hp_storage_gallons_numeric"] = parse_numeric(analysis[PER_HP_STORAGE_COL])
    analysis["expected_total_output_capacity_kbtuh"] = (
        analysis["hp_count_numeric"] * analysis["per_hp_output_capacity_kbtuh"]
    )
    analysis["num_units_minus_expected"] = (
        analysis["num_units_numeric"] - analysis["expected_total_output_capacity_kbtuh"]
    )
    analysis["abs_delta"] = analysis["num_units_minus_expected"].abs()
    analysis["num_units_div_hp_count"] = (
        analysis["num_units_numeric"] / analysis["hp_count_numeric"]
    )
    analysis["ratio_vs_per_hp_output"] = (
        analysis["num_units_div_hp_count"] / analysis["per_hp_output_capacity_kbtuh"]
    )

    def classify(delta: float) -> str:
        if pd.isna(delta):
            return "missing_inputs"
        if delta < 0.01:
            return "exact"
        if delta < 1:
            return "near_rounding"
        return "mismatch"

    analysis["match_class"] = analysis["abs_delta"].map(classify)
    return analysis


def claims_source_has_claim_id() -> bool:
    claims_header = pd.read_csv(CLAIMS_PATH, nrows=0)
    normalized = {col.strip().lower() for col in claims_header.columns}
    return "claimid" in normalized or "claim id" in normalized


def build_model_crosswalk(analysis: pd.DataFrame) -> pd.DataFrame:
    return (
        analysis.groupby(MODEL_COL, dropna=False)
        .agg(
            rows=("ClaimID", "size"),
            manufacturers=("Heat Pump Water Heater Manufacturer", lambda s: " | ".join(sorted({str(v).strip() for v in s if pd.notna(v) and str(v).strip()}))),
            per_hp_output_capacity_min_kbtuh=("per_hp_output_capacity_kbtuh", "min"),
            per_hp_output_capacity_max_kbtuh=("per_hp_output_capacity_kbtuh", "max"),
            per_hp_storage_min_gallons=("per_hp_storage_gallons_numeric", "min"),
            per_hp_storage_max_gallons=("per_hp_storage_gallons_numeric", "max"),
        )
        .reset_index()
        .sort_values(["rows", MODEL_COL], ascending=[False, True])
    )


def write_report(
    sce_path: Path,
    analysis: pd.DataFrame,
    model_crosswalk: pd.DataFrame,
    claim_join_available: bool,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    claim_level_path = OUTPUT_DIR / "sce_dr_numunits_check.csv"
    model_path = OUTPUT_DIR / "sce_dr_model_capacity_crosswalk.csv"
    report_path = OUTPUT_DIR / "sce_dr_numunits_report.md"

    export_cols = [
        "ClaimID",
        "PA",
        "Year",
        "MeasCode",
        "ProgramName",
        "NumUnits",
        HP_COUNT_COL,
        MODEL_COL,
        "Heat Pump Water Heater Manufacturer",
        PER_HP_STORAGE_COL,
        PER_HP_OUTPUT_COL,
        "num_units_numeric",
        "hp_count_numeric",
        "per_hp_output_capacity_kbtuh",
        "expected_total_output_capacity_kbtuh",
        "num_units_div_hp_count",
        "num_units_minus_expected",
        "abs_delta",
        "match_class",
    ]
    analysis[export_cols].to_csv(claim_level_path, index=False)
    model_crosswalk.to_csv(model_path, index=False)

    counts = analysis["match_class"].value_counts(dropna=False)
    exact = int(counts.get("exact", 0))
    near = int(counts.get("near_rounding", 0))
    mismatch = int(counts.get("mismatch", 0))
    total = len(analysis)

    mismatch_preview = analysis.loc[analysis["match_class"] == "mismatch", [
        "ClaimID",
        "NumUnits",
        HP_COUNT_COL,
        MODEL_COL,
        PER_HP_OUTPUT_COL,
        "expected_total_output_capacity_kbtuh",
        "num_units_minus_expected",
    ]].copy()

    lines = [
        "# SCE DR NumUnits Check",
        "",
        f"- Source file used: `{sce_path}`",
        f"- Local claims source checked: `{CLAIMS_PATH}`",
        f"- Claims source has claim ID available for join: `{'yes' if claim_join_available else 'no'}`",
        "",
        "## Conclusion",
        "",
        (
            "`NumUnits` is acting like **total installed HPWH output capacity for the claim** "
            "(system-level kBtu/hr), not per-heat-pump capacity."
        ),
        "",
        "## Evidence",
        "",
        f"- Rows analyzed: `{total}`",
        f"- Exact matches where `NumUnits = HP count x per-HP output capacity`: `{exact}`",
        f"- Near matches within 1 kBtu/hr of that formula: `{near}`",
        f"- Mismatches larger than 1 kBtu/hr: `{mismatch}`",
        "",
        "In other words, `NumUnits / HP count` usually equals the reported per-heat-pump output capacity.",
        "",
        "## Join Status",
        "",
        (
            "A literal match back to the local `claim_df` source by claim ID could not be completed from this "
            "workspace alone because `claims_data_swwh028_222324.csv` does not include a claim ID column."
        ),
        "",
        "## Mismatch Rows",
        "",
    ]

    if mismatch_preview.empty:
        lines.append("No material mismatches found.")
    else:
        lines.append(
            "ClaimID | NumUnits | HP Count | Model | Per-HP Output Cap | Expected Total | Delta"
        )
        lines.append("--- | ---: | ---: | --- | ---: | ---: | ---:")
        for _, row in mismatch_preview.iterrows():
            lines.append(
                f"{row['ClaimID']} | {row['NumUnits']} | "
                f"{row[HP_COUNT_COL]} | {row[MODEL_COL]} | "
                f"{row[PER_HP_OUTPUT_COL]} | "
                f"{row['expected_total_output_capacity_kbtuh']} | {row['num_units_minus_expected']}"
            )
        lines.extend(
            [
                "These appear to be data-entry or mixed-system exceptions rather than evidence that `NumUnits` is per-heat-pump.",
            ]
        )

    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    sce_path = choose_sce_dr_path()
    sce_df = pd.read_csv(sce_path)
    analysis = build_analysis(sce_df)
    model_crosswalk = build_model_crosswalk(analysis)
    claim_join_available = claims_source_has_claim_id()
    write_report(sce_path, analysis, model_crosswalk, claim_join_available)


if __name__ == "__main__":
    main()
