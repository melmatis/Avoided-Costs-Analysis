# Avoided Cost Analysis

Notebook and helper scripts for comparing HPWH model runs, avoided-cost results, and QEA claims outputs.

## Main Files

- `Avoided Costs.ipynb` - primary avoided-cost, TRC, NTG, and sensitivity analysis notebook.
- `QEA Claims Top10 Comparison.ipynb` - QEA claims/model comparison notebook.
- `data_loader.py` - shared loading and cleaning helpers.
- `analyze_sce_dr_numunits.py` - SCE DR number-of-units analysis.
- `compare_model_runs_es_to_swwh028.py` - model run vs. SWWH028 comparison helper.
- `compare_claimed_nunits_to_model_capacity.py` - claimed unit count vs. model capacity comparison helper.

## Outputs

Final summary outputs are kept in `outputs/`. Large hourly/intermediate generated CSVs are ignored by Git.

Tracked deliverables include:

- `outputs/summary_output.csv`
- `outputs/trc_summary_comparison.csv`
- `outputs/ntg_trc_sensitivity.csv`
- `outputs/avoided_cost_analysis_outputs.xlsx`

