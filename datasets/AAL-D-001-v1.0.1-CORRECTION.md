# AAL-D-001 v1.0.1: FX forward exposure correction (2026-10-12)

## What was wrong
In AAL-D-001 v1.0, 11 FX-forward cases recorded `total_exposure_usd` in a foreign currency labelled as USD:

- **9 forward-rate breaks** on USD/xxx pairs, where the key held notional × rate difference without converting it to USD. The rate difference is in the quote currency (KRW, JPY, BRL, CHF, CAD). The 3 xxx/USD forward-rate cases (AUD, EUR) were already correct and are unchanged.
- **2 notional breaks** on xxx/USD pairs (cases 148 and 178), where the key held the notional gap in EUR or AUD as if it were USD.

For the later evaluation runs, the prompt in our run scripts contained the same unit error (it told models to use notional × rate difference and not to invert or divide). The prompt's notional line had the same error: it told models to report the notional difference itself, with no multiplication, so a EUR or AUD gap was reported as USD. These runs' answers are consistent with both lines.

## Corrected values
| Case | Instrument | Old key (USD) | New key (USD, internal-rate conversion) | Also accepted (counterparty-rate conversion) | Accepted range (USD) |
|---|---|---|---|---|---|
| AAL-D-001-147 | USD/KRW NDF 1M | 400,000.00 | 290.28 | 290.29 | 289.28 – 291.29 |
| AAL-D-001-149 | USD/JPY FWD 1M | 1,000,000.00 | 6,349.21 | 6,347.19 | 6,346.19 – 6,350.21 |
| AAL-D-001-153 | USD/BRL NDF 2M | 3,000.00 | 548.45 | 548.40 | 547.40 – 549.45 |
| AAL-D-001-160 | USD/CHF FWD 2M | 24,000.00 | 27,149.32 | 27,057.50 | 27,056.50 – 27,150.32 |
| AAL-D-001-162 | USD/BRL NDF 2M | 1,200.00 | 219.38 | 219.39 | 218.38 – 220.39 |
| AAL-D-001-163 | USD/CAD FWD 1M | 45,000.00 | 32,894.74 | 32,822.76 | 32,821.76 – 32,895.74 |
| AAL-D-001-167 | USD/CHF FWD 2M | 24,000.00 | 27,149.32 | 27,057.50 | 27,056.50 – 27,150.32 |
| AAL-D-001-174 | USD/KRW NDF 1M | 2,400,000.00 | 1,741.65 | 1,741.28 | 1,740.28 – 1,742.65 |
| AAL-D-001-175 | USD/BRL NDF 2M | 30,000.00 | 5,484.46 | 5,479.45 | 5,478.45 – 5,485.46 |

Notional breaks. The gap is converted at the trade's own forward rate. Counterparty and internal rates are the same, so there is no second conversion and no accepted range:

| Case | Instrument | Notional gap | Old key (USD) | New key (USD) |
|---|---|---|---|---|
| AAL-D-001-148 | EUR/USD FWD 1M | €500,000 | 500,000.00 | 542,500.00 (× 1.085) |
| AAL-D-001-178 | AUD/USD FWD 1M | A$600,000 | 600,000.00 | 396,600.00 (× 0.661) |

No other field or case changed, apart from `benchmark_version` (now `1.0.1`) and each corrected case's `version_history`. v1.0 is kept in `datasets/` so earlier published figures can be reproduced.

## Conversion convention
For forward-rate breaks, the new key converts the quote-currency difference to USD at the **internal forward rate**: `USD notional × |counterparty rate − internal rate| / internal forward rate`. This is **AAL's own convention** for scoring a confirmation break. It is not prescribed by ISDA.

As background only: the ISDA 1998 FX and Currency Option Definitions give the non-deliverable settlement amount as Notional × (1 − Forward Rate / Settlement Rate), which divides the rate gap by the rate on the other side of the comparison. See the [User's Guide to the 1998 FX and Currency Option Definitions](https://www.sc.com/en/uploads/sites/66/content/docs/Users-Guide-to-the-1998-FX-and-Currency-Option-Definitions.pdf) and the [2011 amendments (NY Fed FXC)](https://www.newyorkfed.org/medialibrary/microsites/fxc/files/2011/fxc060811b.pdf).

## Scoring tolerance
Scoring accepts any exposure between the internal-rate and the counterparty-rate conversions, ± $1. Each corrected forward-rate case carries this band in `ground_truth.primary_exception.total_exposure_usd_accepted_range`.

## Example
Case AAL-D-001-147: USD/KRW NDF, $8,000,000 notional, counterparty 1,377.95 vs internal 1,378.00, a 0.05 KRW difference. The v1.0 key recorded 400,000 (KRW, labelled USD). The corrected key is 8,000,000 × 0.05 / 1,378.00 = $290.28, and $290.29 (at the counterparty rate) is also accepted. Models that answered $290.28 were correct.
