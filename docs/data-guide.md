# Data Setup Guide

This guide covers two workflows:

1. The scraping team uploads raw STL samples and upload metadata to S3.
2. The preprocessing and training team builds local snapshot and tracks the data directories with DVC.

## Overview for 1 (Pushing data to S3)

1. scraping team uploads raw samples and commits each of their JSONL to GitHub
2. VP then creates the master JSONL index and checks for duplicates:
    1. Aggregate shards
        - Collect all per-run JSONL shards into one working set
        - Validate schema for every row
    2. Check `sample_id` uniqueness
        - Verify every `sample_id` is unique across all rows
        - If any duplicate `sample_id` exists, fail the aggregation and investigate
        - Do not auto-delete here
    3. Group by `sample_fingerprint`
        - Group rows by `sample_fingerprint`
        - Any group with more than one row is an exact-content duplicate set
        - Review each group and choose one row to keep
    4. Remove confirmed duplicates
        - Remove the non-kept rows from the master JSONL
        - Delete the corresponding duplicate sample prefixes from S3
        - Record what was removed in an audit report
    5. Publish master JSONL
        - Write the final deduped `raw_samples.v1.jsonl` using only kept rows

    And sets up and pushes master JSONL to Git (no DVC yet)

3. team can then pull the raw data from S3 using JSONL

## Overview for 2 (Preprocessing and training)

1. Team uses the master JSONL from Git to know which raw samples to pull from S3
2. Team downloads the raw STL data directly from S3 as needed
3. One designated person builds the processed dataset locally
4. That person initializes DVC for the processed dataset only
5. They commit the .dvc files to Git
6. They push the processed dataset to S3 through DVC
7. Other teammates pull:
   - metadata from Git
   - processed dataset from S3 through DVC

## Requirements

- [dvc](https://dvc.org/)
- AWS credentials (Josh)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Definitions for S3

- `dataset_version`: short dataset lineage such as `v1`, `v2`
- `sample_id`: ULID with `smpl_` prefix, for example `smpl_01JQXYZABC123`
- `member_id`: short uploader identifier such as `alice`
- `source_category_hint`: original top-level folder name from the scrape, not a final class label
- `source_project_name`: original project folder name
- `source_project_path`: source-relative path such as `guns/glock_frame_bundle`
- `sample_fingerprint`: deterministic hash of the sorted `(relative_path, sha256)` pairs for all files in the sample

## Proposed S3 Layout

```text
s3://<bucket>/
├── landing/
│   └── raw/
│       └── v1/
│           └── member=<member_id>/
│               └── category=<source_category_hint>/
│                   └── sample=<sample_id>/
│                       ├── original/
│                       │   ├── part1.stl
│                       │   └── part2.stl
│                       └── upload_manifest.json
├── manifests/
│   └── raw/
│       └── v1/
│           ├── uploads/
│           │   └── member=<member_id>/
│           │       └── run=<run_id>.jsonl
│           └── raw_samples.v1.jsonl
├── processed/
│   ├── rendered/
│   │   └── v1/
│   │       └── sample=<sample_id>/
│   │           └── *.png
│   └── labels/
│       └── v1/
└── dvc-cache/
```

Design choices:

- `landing/raw/v1` is append-only upload storage and preserves the original upload trace.
- `manifests/raw/v1/uploads/` stores per-run shards so teammates do not race on one shared S3 object.
- `manifests/raw/v1/raw_samples.v1.jsonl` is the canonical merged raw manifest produced by a maintainer step.
- `processed/` contains derived artifacts only; it is separate from raw uploads.
- `dvc-cache/` is reserved for DVC object storage and should not be mixed with human-readable data.

### S3 Bucket Configuration

Make sure that the following are done before pushing to S3: 

- Bucket owner enforced with ACLs disabled
- Versioning enabled
- Lifecycle rule to abort incomplete multipart uploads after 7 days
- CloudTrail data events if object-level auditing is needed
- Encryption at rest enabled

## Finalized Upload Metadata Schema

The same schema should be used for:

- each per-sample `upload_manifest.json`
- each JSONL row in per-run upload shards
- each JSONL row in the canonical merged raw manifest

```json
{
  "dataset_version": "v1",
  "sample_id": "smpl_01JQXYZABC123",
  "member_id": "alice",
  "uploaded_by": "alice",
  "source_category_hint": "guns",
  "source_project_name": "glock_frame_bundle",
  "source_project_path": "guns/glock_frame_bundle",
  "sample_fingerprint": "sha256:4e2d...",
  "s3_prefix": "s3://<bucket>/landing/raw/v1/member=alice/category=guns/sample=smpl_01JQXYZABC123/",
  "file_count": 2,
  "files": [
    {
      "filename": "frame_left.stl",
      "relative_path": "frame_left.stl",
      "s3_key": "landing/raw/v1/member=alice/category=guns/sample=smpl_01JQXYZABC123/original/frame_left.stl",
      "sha256": "4a7d...",
      "size_bytes": 1234567
    },
    {
      "filename": "frame_right.stl",
      "relative_path": "frame_right.stl",
      "s3_key": "landing/raw/v1/member=alice/category=guns/sample=smpl_01JQXYZABC123/original/frame_right.stl",
      "sha256": "98bc...",
      "size_bytes": 1133557
    }
  ],
  "ingest_status": "uploaded",
  "uploaded_at": "2026-03-28T14:00:00Z",
  "tool_version": "ingest_upload/0.1.0",
  "notes": ""
}
```

Field notes:

- `sample_id` is generated once and never reused.
- `source_category_hint` is for saving scrape organization without implying the final label.
- `source_project_path` is the source-relative path under the local ingest root.
- `sample_fingerprint` is used for duplicate detection across samples even when filenames differ.
- `file_count` should equal `len(files)`.
- `files` must be sorted by `relative_path` before fingerprinting and writing manifests.
- `ingest_status` starts as `uploaded`; later pipeline states can be added if needed.

## 1) Pushing Data to S3

The initial local layout should look like this:

```text
<folder_root>/
├── guns/
│   └── project_a/
│       ├── part1.stl
│       └── part2.stl
└── figurines/
    └── project_b/
        └── body.stl
```

Constraints:

- one `project_folder` equals one sample
- all `.stl` files in a project folder stay together as one sample
- the top-level folder is only a `source_category_hint`
- no SFW, grey-area, or NSFW class is assigned at ingest time

### Local Folder Contract

Each teammate should prepare data as:

```text
<root>/<source_category_hint>/<project_folder>/*.stl
```

For `v1`, a sample folder may contain one or more `.stl` files, but nested subdirectories inside a sample are not supported. A single sample folder can have multiple STL files but not nested folders.

### Pre-upload Validation

Before upload, the ingest script checks:

- sample folder contains at least one `.stl`
- every file has `.stl` extension
- no empty files
- no duplicate filenames within the sample
- SHA-256 hashes can be computed for every file
- destination sample prefix does not already exist

### Upload Flow

Each teammate runs one command:

```bash
uv run python scripts/ingest_upload.py \
  --member alice \
  --root /path/to/local/data \
  --bucket <bucket> \
  --dataset-version v1
```

1. traverse the local folder tree
2. generate a new `sample_id` for each valid sample
3. compute file hashes, sizes, and the `sample_fingerprint`
4. upload files under `landing/raw/v1/.../original/`
5. write one `upload_manifest.json` beside the uploaded files
6. write one local per-run JSONL file for review
7. upload that per-run JSONL shard to `manifests/raw/v1/uploads/...`

### Merge Manifest

A maintainer merges all per-run JSONL shards into:

```text
manifests/raw/v1/raw_samples.v1.jsonl
```

### Validate Merged Manifest

Validation should check:

- `sample_id` values are unique
- all referenced S3 keys exist
- hashes and sizes match S3 objects when validation mode requires it
- no sample has zero files
- no duplicate `sample_fingerprint` values unless explicitly allowed

### Freeze Canonical Raw Snapshot

Only after validation, create the canonical local raw snapshot under `data/raw/v1` and track that directory with DVC.

## 2) DVC Setup

Install and initialize DVC:

```bash
uv pip install "dvc[s3]"

dvc init

dvc remote add -d s3remote s3://<bucket>/dvc-cache
dvc remote modify s3remote region <aws-region>

# prefer AWS profile-based auth
dvc remote modify --local s3remote profile <profile-name>

# or, if needed:
# dvc remote modify --local s3remote access_key_id ...
# dvc remote modify --local s3remote secret_access_key ...
```

Track the data directories with DVC:

```bash
dvc add data/raw/v1
dvc add data/processed/rendered/v1

git add data/raw/v1.dvc data/processed/rendered/v1.dvc .gitignore .dvc/config data/manifests/raw/raw_samples.v1.jsonl
git commit -m "Track canonical raw and rendered datasets with DVC"
dvc push
```

Notes:

- DVC tracks large data directories, not the JSONL manifest by default.
- `data/manifests/raw/raw_samples.v1.jsonl` stays in Git as a readable index for review and debugging.
- The DVC remote points at `s3://<bucket>/dvc-cache`, which should remain separate from upload and processed prefixes.

## Repo Structure

```text
data/
├── manifests/
│   ├── raw/
│   │   └── raw_samples.v1.jsonl
│   ├── curated/
│   │   └── dataset_full.v1.jsonl
│   └── benchmark/
│       └── dataset_benchmark.v1.jsonl
├── raw/
│   └── v1/
└── processed/
    └── rendered/
        └── v1/

scripts/
├── ingest_upload.py
├── merge_raw_manifests.py
├── validate_manifest.py
├── canonicalize_raw.py
└── render_openscad.py
```

## `v1` Sample Shape In Bucket 

For `v1`, a sample is one project folder containing one or more `.stl` files directly inside the folder:

```text
<root>/<source_category_hint>/<project_folder>/*.stl
```

This means:

- one project folder equals one sample
- a sample may contain multiple STL parts
- nested subdirectories inside a sample are out of scope for `v1`

This keeps the first ingest and canonicalization scripts simpler, avoids path edge cases, and still matches the current raw data shape.
