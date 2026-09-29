# Quality Assurance Checklist

**AAL-D-001 · v1.0**

Complete before marking the dataset as `published`.

---

## Dataset Integrity

| # | Check | Status |
|---|---|---|
| 1 | All cases pass `validate.py` without errors | ☐ |
| 2 | No duplicate case IDs | ☐ |
| 3 | All case IDs follow `AAL-D-001-{SEQ}` format | ☐ |
| 4 | `benchmark_version` is consistent across all cases | ☐ |

## Difficulty Distribution

| # | Check | Target | Status |
|---|---|---|---|
| 5 | Easy cases | 100 (40%) | ☐ |
| 6 | Moderate cases | 100 (40%) | ☐ |
| 7 | Complex cases | 50 (20%) | ☐ |
| 8 | Total cases | 250 | ☐ |

## Asset Class Coverage

| # | Check | Status |
|---|---|---|
| 9 | Equities represented | ☐ |
| 10 | Fixed income represented | ☐ |
| 11 | Listed futures represented | ☐ |
| 12 | Options represented | ☐ |
| 13 | Interest rate swaps represented | ☐ |
| 14 | FX forwards represented | ☐ |
| 15 | Credit products represented | ☐ |

## Exception Coverage

| # | Check | Status |
|---|---|---|
| 16 | Clean-match cases (no exception) included — target ~30% | ☐ |
| 17 | All 16 exception categories represented | ☐ |
| 18 | Risk levels 1–5 all represented | ☐ |

## Ground Truth

| # | Check | Status |
|---|---|---|
| 19 | Every case has complete ground truth | ☐ |
| 20 | Ground truth constructed before model evaluation | ☐ |
| 21 | Minimum 2 independent reviewers per case | ☐ |
| 22 | Reviewer qualifications documented | ☐ |
| 23 | All `confidence` values are valid (`definitive`, `expert_consensus`, `conditional`) | ☐ |

## Schema Compliance

| # | Check | Status |
|---|---|---|
| 24 | All cases conform to `schemas/benchmark_case.json` | ☐ |
| 25 | All `input` objects have both `counterparty_confirmation` and `internal_record` | ☐ |
| 26 | All `failure_modes` reference valid FM codes | ☐ |
| 27 | Complex cases have ≥ 2 failure modes | ☐ |
| 28 | All cases have `version_history` with at least one entry | ☐ |

## Data Quality

| # | Check | Status |
|---|---|---|
| 29 | No real institution names, fund names, or person names | ☐ |
| 30 | All CUSIPs/ISINs are synthetic or non-identifying | ☐ |
| 31 | Numeric values are realistic for the asset class | ☐ |
| 32 | Settlement conventions are correct for the market | ☐ |
| 33 | Business context is operationally accurate | ☐ |

---

*AAL-D-001 · QA Checklist · v1.0 · AI Alpha Labs*
