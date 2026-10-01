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
- The case identifier shown to models in AAL-D-008 prompts includes the benchmark name (for example AAL-D-008-0001). We did not test whether this affects behavior. The AAL-D-006 and AAL-D-007 prompts do not carry one.
- AAL-D-001's ground truth was constructed by the author with AI assistance, and no independent human reviewers were involved. The label `expert_consensus`, used on 11 of its 250 cases, does not denote a panel of reviewers.

## Licenses

Code is MIT licensed (see `LICENSE`). Datasets are licensed CC BY 4.0 (see `DATA_LICENSE.md`).

## Contact

joe@aialphalabs.ai
