# Currency-unit corrections: AAL-D-002, AAL-D-003, AAL-D-005 (2026-10-12)

Source: the FX currency-unit audit of the D-001 to D-005 answer keys (2026-10-12). Earlier versions stay in `datasets/` so published figures can still be reproduced. Every changed case has a `version_history` entry dated 2026-10-12.

## AAL-D-002 v1.0.2 (from v1.0.1)
- **Case 146:** the counterparty issued its margin call of 1,900,000 in EUR, but `difference_unit` was `usd`. It is now `eur`. No value changed, and `correct_call_amount` (0) is unaffected.

## AAL-D-003 v1.0.3 (from v1.0.2)
The exposure values in these cases are EUR amounts stored in `total_exposure_usd`. The cases contain no EUR/USD rate, so **no value was converted, no exchange rate was added, and no value changed.** Each affected exception now has a `total_exposure_label` field. The field name `total_exposure_usd` is kept so existing scorers still run.
- **44 Eurex cases traded in EUR**, labelled `exposure in trade currency (EUR)`: 001, 002, 010, 018, 039, 052, 055, 056, 057, 063, 064, 066, 067, 071, 072, 075, 077, 078, 080, 083, 084, 085, 087, 094, 104, 112, 137, 139, 142, 152, 156, 159, 163, 164, 166, 168, 171, 172, 185, 189, 191, 193, 198, 199. Metrics Desk cross-checked them.
- **Cases 141, 183 and 197** are currency breaks: the counterparty confirmed in USD, while the internal record is in EUR. They are labelled `exposure in EUR (internal record currency)`.

## AAL-D-005 v1.0.1 (from v1.0)
On cases 158 to 162 (BRK-CCY booking-currency breaks), v1.0 computed `break_amount` by subtracting the front-office P&L from the product-control P&L without converting currencies. The front-office figure is now converted into the product-control currency using the case's own `fx_rates` (all quoted against USD), and then subtracted.

| Case | Currencies (FO vs PC) | Old break_amount | New break_amount |
|---|---|---|---|
| AAL-D-005-158 | EUR vs USD | 92,462.39 | 95,920.32 USD |
| AAL-D-005-159 | GBP vs JPY | 70,972.05 | 8,526,118.23 JPY |
| AAL-D-005-160 | GBP vs USD | 82,102.93 | 79,020.03 USD |
| AAL-D-005-161 | CHF vs EUR | 168,476.40 | 179,351.73 EUR |
| AAL-D-005-162 | CHF vs EUR | 194,061.53 | −189,846.26 EUR |

- **Case 162 sign:** v1.0 recorded a positive break, but the product-control residual is negative. The corrected value is negative.
- **Text updated:** each case's `recommended_action` now quotes the new amount.
- **Scoring:** `break_amount` is not in D-005's `scoring_criteria`, so published D-005 scores are not affected.
- **Not changed:** the input field `product_control_pnl.unexplained_residual` still shows the system's unconverted figure, as part of the scenario.
