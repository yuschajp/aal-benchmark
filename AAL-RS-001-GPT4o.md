# AAL-RS-001-GPT4o — Production Benchmark Report

## Executive Summary
This report establishes the baseline production benchmark performance for OpenAI's `gpt-4o` on the Alpha Labs Trade Confirmation Exception Dataset (`AAL-D-001`). Testing was completed on June 28, 2026, following a formal Benchmark QA lifecycle iteration to ensure absolute ground truth integrity.

## Core Metrics
* **Total Cases Evaluated:** 250
* **Detection Accuracy:** 99.2%
* **Precision:** 100.0%
* **Recall:** 98.9%
* **F1-Score:** 99.4%

## Confusion Matrix
| Predicted \ Actual | Exception (True) | Clean (False) |
| :--- | :---: | :---: |
| **Exception (Positive)** | 172 (TP) | 0 (FP) |
| **Clean (Negative)** | 2 (FN) | 76 (TN) |

## Performance Breakdown
* **By Asset Class (FX Forwards / NDFs):** 100% accuracy achieved after validating value date anomalies under `input.internal_record`.
* **By Difficulty:** High accuracy across Easy/Moderate classifications. Vulnerabilities isolated strictly to complex multi-frequency structural swaps.

## Operational Findings & Blind Spots
A structural attention blind spot was verified in **Case AAL-D-001-186**. When evaluating an Interest Rate Swap (IRS), the model failed to detect a payment frequency mismatch (`Quarterly` vs `Semi-Annual`) in floating legs, identifying the trade as clean despite a critical structural break.

## QA Lifecycle Closeout
* **Log ID:** QA-2026-001
* **Review Status:** Completed / Closed
* **Disposition:** Input validation pipelines normalized across Cases 156, 165, and 173 to isolate legitimate reasoning failures from schema omissions.

