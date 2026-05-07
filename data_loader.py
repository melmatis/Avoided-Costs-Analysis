import re
from pathlib import Path

import pandas as pd


WORKBOOK = Path("2024 Electric ACC_SCE_CZ6.xlsx")
GAS_ACC_WORKBOOK = Path("2024 Gas ACC_SCG_Comm.xlsx")
HPWH_WORKBOOK = Path("CZ6_Hotel_CODE_DEERWHCalc2026.xlsx")
HPWH_WORKBOOK_PATTERN = re.compile(
    r"^CZ(?P<climate_zone>[A-Za-z0-9]+)_(?P<building_type>[^_]+)_(?P<baseline_type>CODE|EXISTING)_DEERWHCalc(?P<version>\d+)?\.xlsx$",
    re.IGNORECASE,
)
AVOIDED_COST_SHEETS = [
    "Air Quality Adder",
    "Avoided AS Procurement",
    "Distribution Cap",
    "Energy",
    "GHG Adder",
    "GHG Cap and Trade",
    "GHG Rebalance",
    "Generation Cap",
    "Losses",
    "Methane Leakage",
    "Transmission Cap",
    "annual",
]
EMISSION_RATE_SHEET = "Emission Rates"
BTU_PER_THERM = 100_000


def _read_or_build_csv_cache(cache: Path, sources: list[Path], builder) -> pd.DataFrame:
    cache = Path(cache)
    sources = [Path(source) for source in sources]
    if cache.exists() and all(cache.stat().st_mtime >= source.stat().st_mtime for source in sources):
        return pd.read_csv(cache)

    df = builder()
    cache.parent.mkdir(exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def _normalize_climate_zone(climate_zone: str | int) -> str:
    value = str(climate_zone).strip().upper()
    if value.startswith("CZ"):
        value = value[2:]
    match = re.fullmatch(r"(\d+)[A-Z]", value)
    if match:
        value = match.group(1)
    return value


def _normalize_building_type(building_type: str) -> str:
    value = re.sub(r"[^A-Z0-9]+", "", str(building_type).strip().upper())
    aliases = {
        "GRO": "GROCERY",
        "GROCERYSTORE": "GROCERY",
        "HTL": "HOTEL",
        "LODGINGHOTEL": "HOTEL",
    }
    return aliases.get(value, value)


def _normalize_baseline_type(baseline_type: str) -> str:
    return str(baseline_type).strip().upper()


def parse_hpwh_workbook_name(workbook: Path | str) -> dict[str, str] | None:
    workbook = Path(workbook)
    match = HPWH_WORKBOOK_PATTERN.match(workbook.name)
    if not match:
        return None
    parts = match.groupdict()
    return {
        "climate_zone": _normalize_climate_zone(parts["climate_zone"]),
        "building_type": _normalize_building_type(parts["building_type"]),
        "baseline_type": _normalize_baseline_type(parts["baseline_type"]),
        "version": parts.get("version") or "",
        "path": str(workbook),
        "filename": workbook.name,
    }


def list_hpwh_workbooks(directory: Path | None = None) -> pd.DataFrame:
    search_dir = Path(directory) if directory is not None else Path(__file__).resolve().parent
    matches: list[dict[str, str]] = []
    for workbook in sorted(search_dir.glob("*DEERWHCalc*.xlsx")):
        parsed = parse_hpwh_workbook_name(workbook)
        if parsed is not None:
            matches.append(parsed)

    columns = ["climate_zone", "building_type", "baseline_type", "version", "filename", "path"]
    if not matches:
        return pd.DataFrame(columns=columns)

    return pd.DataFrame(matches, columns=columns).sort_values(
        ["climate_zone", "building_type", "baseline_type", "filename"]
    ).reset_index(drop=True)


def resolve_hpwh_workbook(
    climate_zone: str | int,
    building_type: str,
    baseline_type: str,
    directory: Path | None = None,
) -> Path:
    available = list_hpwh_workbooks(directory=directory)
    if available.empty:
        raise FileNotFoundError("No HPWH workbook files matching the expected naming pattern were found.")

    climate_zone_key = _normalize_climate_zone(climate_zone)
    building_type_key = _normalize_building_type(building_type)
    baseline_type_key = _normalize_baseline_type(baseline_type)

    selected = available[
        (available["climate_zone"] == climate_zone_key)
        & (available["building_type"] == building_type_key)
        & (available["baseline_type"] == baseline_type_key)
    ]

    if selected.empty:
        available_keys = available[["climate_zone", "building_type", "baseline_type", "filename"]]
        raise FileNotFoundError(
            "No HPWH workbook matched the requested selection "
            f"(climate_zone={climate_zone!r}, building_type={building_type!r}, baseline_type={baseline_type!r}).\n"
            f"Available options:\n{available_keys.to_string(index=False)}"
        )

    if len(selected) > 1:
        raise ValueError(
            "Multiple HPWH workbooks matched the requested selection. "
            f"Please disambiguate the filenames:\n{selected[['filename', 'path']].to_string(index=False)}"
        )

    return Path(selected.iloc[0]["path"])


def get_claims_building_type_regex(building_type: str) -> str:
    normalized = _normalize_building_type(building_type)
    aliases = {
        "HOTEL": r"(?:^htl$|lodging\s*-\s*hotel|hotel)",
        "GROCERY": r"(?:^gro$|grocery)",
        "MFM": r"(?:^mfm$|multifamily)",
        "HSP": r"(?:^hsp$|hospital|health/medical\s*-\s*hospital)",
        "NRS": r"(?:^nrs$|nursing|health/medical\s*-\s*nursing\s*home)",
    }
    if normalized in aliases:
        return aliases[normalized]

    token = re.escape(str(building_type).strip())
    return rf"(?:^{token}$|{token})"


def _read_hourly_sheets(
    workbook: Path,
    sheets: list[str],
    value_name: str,
) -> pd.DataFrame:
    """Read 8760-by-year tabs into a tidy category/year/hour table."""
    workbook = Path(workbook)
    available_sheets = set(pd.ExcelFile(workbook, engine="openpyxl").sheet_names)
    missing_sheets = [sheet for sheet in sheets if sheet not in available_sheets]
    if missing_sheets:
        raise ValueError(f"Workbook is missing expected sheet(s): {missing_sheets}")

    frames = []
    for sheet in sheets:
        df = pd.read_excel(
            workbook,
            sheet_name=sheet,
            header=1,
            engine="openpyxl",
        )

        df = df.loc[:, df.columns.notna()]
        if "Hour" not in df.columns:
            raise ValueError(f"Sheet {sheet!r} does not have a 'Hour' column.")

        year_columns = [col for col in df.columns if isinstance(col, int)]
        if not year_columns:
            raise ValueError(f"Sheet {sheet!r} does not have year columns.")

        hourly = df[["Hour", *year_columns]].copy()
        hourly = hourly[hourly["Hour"].notna()]
        hourly["Hour"] = hourly["Hour"].astype(int)

        long = hourly.melt(
            id_vars="Hour",
            value_vars=year_columns,
            var_name="year",
            value_name=value_name,
        )
        long.insert(0, "category", sheet)
        frames.append(long)

    hourly_values = pd.concat(frames, ignore_index=True)
    hourly_values["year"] = hourly_values["year"].astype(int)
    hourly_values = hourly_values.rename(columns={"Hour": "hour"})
    hourly_values = hourly_values[["category", "year", "hour", value_name]]

    counts = hourly_values.groupby(["category", "year"]).size()
    bad_counts = counts[counts != 8760]
    if not bad_counts.empty:
        raise ValueError(
            "Expected 8,760 rows for every category/year, but found:\n"
            f"{bad_counts.to_string()}"
        )

    return hourly_values


def read_hourly_avoided_costs(workbook: Path = WORKBOOK) -> pd.DataFrame:
    """Read hourly cost-effectiveness avoided-cost tabs."""
    avoided_costs = _read_hourly_sheets(
        workbook=workbook,
        sheets=AVOIDED_COST_SHEETS,
        value_name="avoided_cost",
    )
    return avoided_costs


def read_total_hourly_avoided_costs(workbook: Path = WORKBOOK) -> pd.DataFrame:
    """Read only the annual total hourly avoided-cost tab."""
    workbook = Path(workbook)
    return _read_or_build_csv_cache(
        cache=Path("outputs") / "_cache_electric_annual_avoided_costs.csv",
        sources=[workbook],
        builder=lambda: _read_hourly_sheets(
            workbook=workbook,
            sheets=["annual"],
            value_name="avoided_cost",
        ),
    )


def read_emission_rates(workbook: Path = WORKBOOK) -> pd.DataFrame:
    """Read hourly emissions rates separately from avoided-cost dollar streams."""
    emission_rates = _read_hourly_sheets(
        workbook=workbook,
        sheets=[EMISSION_RATE_SHEET],
        value_name="emission_rate_tonnes_per_kwh",
    )
    return emission_rates.drop(columns="category")


def read_gas_avoided_costs(workbook: Path = GAS_ACC_WORKBOOK) -> pd.DataFrame:
    """Read monthly gas avoided-cost components in $/therm."""
    workbook = Path(workbook)
    df = pd.read_excel(workbook, sheet_name=0, engine="openpyxl")
    df = df.loc[:, df.columns.notna()]

    required_columns = {"Month", "Category"}
    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        raise ValueError(
            f"Gas workbook is missing expected column(s): {sorted(missing_columns)}"
        )

    year_columns = [
        col
        for col in df.columns
        if isinstance(col, int) or (isinstance(col, float) and float(col).is_integer())
    ]
    if not year_columns:
        raise ValueError(f"No year columns found in {workbook}.")

    records = []
    for _, row in df.iterrows():
        month = row["Month"]
        category = row["Category"]
        if pd.isna(month) or pd.isna(category):
            continue
        for year_col in year_columns:
            year = int(year_col)
            value = row[year_col]
            records.append(
                {
                    "month": str(month),
                    "category": str(category),
                    "year": year,
                    "gas_avoided_cost_per_therm": value,
                }
            )

    gas_avoided_costs = pd.DataFrame(records)
    gas_avoided_costs["gas_avoided_cost_per_therm"] = pd.to_numeric(
        gas_avoided_costs["gas_avoided_cost_per_therm"],
        errors="coerce",
    )
    gas_avoided_costs = gas_avoided_costs.dropna(
        subset=["gas_avoided_cost_per_therm"]
    )
    gas_avoided_costs = gas_avoided_costs[["month", "category", "year", "gas_avoided_cost_per_therm"]]

    return gas_avoided_costs


def read_hpwh_load_shapes(
    workbook: Path | None = None,
    climate_zone: str | int | None = None,
    building_type: str | None = None,
    baseline_type: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read baseline gas and HPWH electric hourly load shapes."""
    if workbook is None:
        if climate_zone is not None and building_type is not None and baseline_type is not None:
            workbook = resolve_hpwh_workbook(
                climate_zone=climate_zone,
                building_type=building_type,
                baseline_type=baseline_type,
            )
        else:
            workbook = HPWH_WORKBOOK
    workbook = Path(workbook)

    gas = pd.read_excel(workbook, sheet_name="Gas", header=1, engine="openpyxl")
    gas = gas.loc[:, gas.columns.notna()]
    gas = gas.iloc[:, [0, 8]].copy()
    gas.columns = ["Hour", "Input"]
    gas = gas[gas["Hour"].notna()]
    gas["hour"] = gas["Hour"].astype(int)
    gas["baseline_gas_btu"] = pd.to_numeric(gas["Input"], errors="coerce")
    gas["baseline_gas_therms"] = gas["baseline_gas_btu"] / BTU_PER_THERM
    gas_load = gas[["hour", "baseline_gas_btu", "baseline_gas_therms"]].reset_index(
        drop=True
    )

    elec = pd.read_excel(workbook, sheet_name="Elec", header=2, engine="openpyxl")
    elec = elec.loc[:, elec.columns.notna()]
    elec = elec[["Hour", "Total input power"]].copy()
    elec = elec[elec["Hour"].notna()]
    elec["hour"] = elec["Hour"].astype(int)
    elec["hpwh_electric_kw"] = pd.to_numeric(
        elec["Total input power"],
        errors="coerce",
    )
    elec["hpwh_electric_kwh"] = elec["hpwh_electric_kw"]
    elec_load = elec[["hour", "hpwh_electric_kw", "hpwh_electric_kwh"]].reset_index(
        drop=True
    )

    for name, load in [("Gas", gas_load), ("Elec", elec_load)]:
        if len(load) != 8760:
            raise ValueError(f"Expected 8,760 hourly rows in {name}, found {len(load)}.")

    return gas_load, elec_load


if __name__ == "__main__":
    avoided_costs = read_hourly_avoided_costs()
    emission_rates = read_emission_rates()
    gas_avoided_costs = read_gas_avoided_costs()
    baseline_gas_load, hpwh_electric_load = read_hpwh_load_shapes()

    categories = sorted(avoided_costs["category"].unique())
    years = sorted(avoided_costs["year"].unique())

    print("ELECTRIC ACC HOURLY IMPORT")
    print(f"Workbook: {WORKBOOK}")
    print(f"Rows: {len(avoided_costs):,}")
    print(f"Categories: {len(categories)}")
    print(f"Years: {years[0]}-{years[-1]} ({len(years)} years)")
    print()
    print("Rows per category/year:")
    print(avoided_costs.groupby(["category", "year"]).size().head(20).to_string())
    print()
    print("Preview:")
    print(avoided_costs.head(12).to_string(index=False))
    print()
    print("EMISSION RATES")
    print(f"Rows: {len(emission_rates):,}")
    print(emission_rates.head(12).to_string(index=False))
    print()
    print("GAS ACC IMPORT")
    print(f"Rows: {len(gas_avoided_costs):,}")
    print(gas_avoided_costs.head(12).to_string(index=False))
    print()
    print("HPWH LOAD SHAPES")
    print(f"Gas rows: {len(baseline_gas_load):,}")
    print(f"Electric rows: {len(hpwh_electric_load):,}")
