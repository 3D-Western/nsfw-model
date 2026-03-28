# Data Pushing

This folder contains a minimal ingest uploader for raw STL samples.

## Setup

```bash
uv sync
```

## Usage: pushing to S3 

```bash
uv run python scripts/ingest_upload.py \
  --member alice \
  --root /path/to/local/data \
  --bucket your-bucket \
  --dataset-version v1
```

Optional flags:

- `--uploaded-by` defaults to `--member`
- `--notes` adds freeform notes to each row
- `--aws-profile` uses a specific local AWS profile
- `--output-jsonl` sets local output path for the per-run shard

## Local input contract

```text
<root>/<source_category_hint>/<project_folder>/*.stl
```

Each `project_folder` is one sample.

## What the script does 

- Stops immediately on the first invalid sample (check `docs/data-guide.md` for rules for samples)
- Generates ULID (`smpl_<ULID>`)
- Writes `upload_manifest.json` for each uploaded sample
- Writes one per-run local JSONL shard and uploads it to `manifests/raw/<dataset_version>/uploads/member=<member>/run=<run_id>.jsonl`
