# AI Alpha Labs benchmark framework

Dataset generators, deterministic scorers, evaluation drivers and results for the AI Alpha Labs benchmark series, AAL-D-001 through AAL-D-008. Methodology and results are published at [aialphalabs.ai/research](https://www.aialphalabs.ai/research).

## What is here

- Generators that build each dataset, with each case's correct answer fixed before any model sees it.
- Scorers that are deterministic code. No model grades another model.
- Drivers that run a model against a dataset three times per case and record the serving host, settings and reasoning tokens, for example `run_d008_baseline.py --model gpt-5.6-sol`.
- Frozen datasets in `datasets/`. Use the latest version of each. Earlier versions are kept so earlier published figures can be reproduced, and some were superseded after defects were found.
- Results in the `eval_out_*` folders.

## Reproducing a result

Set an API key for the provider you want to test, then run the driver for the dataset. Each dataset page on the website lists the models, run settings and known limitations for that benchmark.

## Known limitations

- Cases are generated to model real operations workflows. They are not drawn from live trade records.
- Several parameters are assumptions, disclosed on each dataset page.
- Model results depend on how the model is served. See the AAL-D-008 page on host reasoning defaults.
- Some early result files carry internal evaluation identifiers (AAL-EVAL-*) from before the current numbering.
- AAL-D-001 marks 11 of its 250 cases `expert_consensus` and the rest `definitive`. The review process behind that label is not documented in this repository.

## Licenses

Code is MIT licensed (see `LICENSE`). Datasets are licensed CC BY 4.0 (see `DATA_LICENSE.md`).

## Contact

joe@aialphalabs.ai
