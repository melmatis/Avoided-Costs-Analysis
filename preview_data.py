import pandas as pd
from pathlib import Path

csv_file = Path.cwd() / 'electric_avoided_costs.csv'
df = pd.read_csv(csv_file)

print("=" * 70)
print("ELECTRIC AVOIDED COSTS IMPORT SUMMARY")
print("=" * 70)
print(f"\nFile: {csv_file.name}")
print(f"Total records: {len(df):,}")
print(f"\nShape: {df.shape}")

print(f"\nData preview:")
print(df.head(10).to_string())

print(f"\n\nUtilities: {', '.join(sorted(df['utility'].unique()))}")
print(f"Climate zones: {', '.join(sorted(df['climate_zone'].unique()))}")
print(f"Years: {df['year'].min()}-{df['year'].max()}")

print(f"\n\nRecords by utility:")
for util in sorted(df['utility'].unique()):
    count = len(df[df['utility'] == util])
    avg = df[df['utility'] == util]['avoided_cost_annual_avg'].mean()
    print(f"  {util}: {count:3d} records (avg: ${avg:7.2f})")

print(f"\n\nStatistics:")
print(f"  Min avoided cost: ${df['avoided_cost_annual_avg'].min():.4f}")
print(f"  Max avoided cost: ${df['avoided_cost_annual_avg'].max():.2f}")
print(f"  Mean avoided cost: ${df['avoided_cost_annual_avg'].mean():.4f}")
