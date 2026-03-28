#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import ulid


TOOL_VERSION = "ingest_upload/0.1.0"


@dataclass
class FileRecord:
    filename: str
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass
class SampleRecord:
    source_category_hint: str
    source_project_name: str
    source_project_path: str
    files: list[FileRecord]
    sample_fingerprint: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload raw STL samples and write per-run JSONL shard"
    )
    parser.add_argument("--member", required=True, help="Uploader member_id")
    parser.add_argument("--root", required=True, help="Local ingest root path")
    parser.add_argument("--bucket", required=True, help="Destination S3 bucket")
    parser.add_argument(
        "--dataset-version", required=True, help="Dataset version like v1"
    )
    parser.add_argument(
        "--uploaded-by", default=None, help="Uploaded by identity; defaults to --member"
    )
    parser.add_argument(
        "--notes", default="", help="Optional notes stored in manifests"
    )
    parser.add_argument("--aws-profile", default=None, help="Optional AWS profile name")
    parser.add_argument(
        "--output-jsonl", default=None, help="Optional local JSONL output path"
    )
    return parser.parse_args()


def sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_fingerprint(files: list[FileRecord]) -> str:
    parts = [f"{item.relative_path}\t{item.sha256}" for item in files]
    canonical = "\n".join(parts).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return f"sha256:{digest}"


def validate_and_collect_samples(root: Path) -> list[SampleRecord]:
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Root path does not exist or is not a directory: {root}")

    samples: list[SampleRecord] = []
    categories = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name)

    for category_dir in categories:
        projects = sorted(
            [p for p in category_dir.iterdir() if p.is_dir()], key=lambda p: p.name
        )

        for project_dir in projects:
            source_category_hint = category_dir.name
            source_project_name = project_dir.name
            source_project_path = f"{source_category_hint}/{source_project_name}"

            nested_dirs = [p for p in project_dir.iterdir() if p.is_dir()]
            if nested_dirs:
                first = nested_dirs[0].name
                raise ValueError(
                    f"Invalid sample '{source_project_path}': nested subdirectories are not supported (found '{first}')"
                )

            files_in_folder = [p for p in project_dir.iterdir() if p.is_file()]
            if not files_in_folder:
                raise ValueError(
                    f"Invalid sample '{source_project_path}': sample folder is empty"
                )

            stl_files: list[Path] = []
            for file_path in files_in_folder:
                if file_path.suffix.lower() != ".stl":
                    raise ValueError(
                        f"Invalid sample '{source_project_path}': found non-STL file '{file_path.name}'"
                    )
                stl_files.append(file_path)

            if not stl_files:
                raise ValueError(
                    f"Invalid sample '{source_project_path}': sample folder has no .stl files"
                )

            filenames = [f.name for f in stl_files]
            if len(filenames) != len(set(filenames)):
                raise ValueError(
                    f"Invalid sample '{source_project_path}': duplicate filenames in sample folder"
                )

            records: list[FileRecord] = []
            for file_path in sorted(stl_files, key=lambda p: p.name):
                size_bytes = file_path.stat().st_size
                if size_bytes <= 0:
                    raise ValueError(
                        f"Invalid sample '{source_project_path}': empty file '{file_path.name}'"
                    )
                file_sha256 = sha256_file(file_path)
                records.append(
                    FileRecord(
                        filename=file_path.name,
                        relative_path=file_path.name,
                        sha256=file_sha256,
                        size_bytes=size_bytes,
                    )
                )

            records = sorted(records, key=lambda item: item.relative_path)
            sample_fingerprint = build_fingerprint(records)
            samples.append(
                SampleRecord(
                    source_category_hint=source_category_hint,
                    source_project_name=source_project_name,
                    source_project_path=source_project_path,
                    files=records,
                    sample_fingerprint=sample_fingerprint,
                )
            )

    if not samples:
        raise ValueError(f"No samples found under root '{root}'")

    return samples


def s3_prefix_exists(s3_client: Any, bucket: str, prefix: str) -> bool:
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    return bool(response.get("KeyCount", 0))


def upload_file(s3_client: Any, bucket: str, key: str, file_path: Path) -> None:
    s3_client.upload_file(str(file_path), bucket, key)


def upload_json(s3_client: Any, bucket: str, key: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, sort_keys=False, separators=(",", ":")).encode("utf-8")
    s3_client.put_object(
        Bucket=bucket, Key=key, Body=body, ContentType="application/json"
    )


def make_sample_id() -> str:
    return f"smpl_{str(ulid.new())}"


def make_run_id() -> str:
    return str(ulid.new())


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def main() -> None:
    args = parse_args()
    member = args.member.strip()
    uploaded_by = args.uploaded_by.strip() if args.uploaded_by else member
    root = Path(args.root).expanduser().resolve()
    bucket = args.bucket.strip()
    dataset_version = args.dataset_version.strip()
    notes = args.notes

    if not member:
        raise ValueError("--member cannot be empty")
    if not bucket:
        raise ValueError("--bucket cannot be empty")
    if not dataset_version:
        raise ValueError("--dataset-version cannot be empty")

    samples = validate_and_collect_samples(root)

    session_kwargs: dict[str, str] = {}
    if args.aws_profile:
        session_kwargs["profile_name"] = args.aws_profile
    session = boto3.session.Session(**session_kwargs)
    s3_client = session.client("s3")

    run_id = make_run_id()
    local_output = (
        Path(args.output_jsonl).expanduser().resolve()
        if args.output_jsonl
        else Path(
            f"manifests/raw/{dataset_version}/uploads/member={member}/run={run_id}.jsonl"
        ).resolve()
    )
    local_output.parent.mkdir(parents=True, exist_ok=True)

    jsonl_rows: list[dict[str, Any]] = []

    for sample in samples:
        sample_id = make_sample_id()
        sample_prefix = (
            f"landing/raw/{dataset_version}/member={member}/category={sample.source_category_hint}/"
            f"sample={sample_id}/"
        )

        if s3_prefix_exists(s3_client, bucket, sample_prefix):
            raise ValueError(
                f"Destination sample prefix already exists in S3: s3://{bucket}/{sample_prefix}"
            )

        files_payload: list[dict[str, Any]] = []
        for file_item in sample.files:
            s3_key = f"{sample_prefix}original/{file_item.filename}"
            local_path = root / sample.source_project_path / file_item.filename
            upload_file(s3_client, bucket, s3_key, local_path)
            files_payload.append(
                {
                    "filename": file_item.filename,
                    "relative_path": file_item.relative_path,
                    "s3_key": s3_key,
                    "sha256": file_item.sha256,
                    "size_bytes": file_item.size_bytes,
                }
            )

        uploaded_at = utc_now_iso()
        row: dict[str, Any] = {
            "dataset_version": dataset_version,
            "sample_id": sample_id,
            "member_id": member,
            "uploaded_by": uploaded_by,
            "source_category_hint": sample.source_category_hint,
            "source_project_name": sample.source_project_name,
            "source_project_path": sample.source_project_path,
            "sample_fingerprint": sample.sample_fingerprint,
            "s3_prefix": f"s3://{bucket}/{sample_prefix}",
            "file_count": len(files_payload),
            "files": files_payload,
            "ingest_status": "uploaded",
            "uploaded_at": uploaded_at,
            "tool_version": TOOL_VERSION,
            "notes": notes,
        }

        upload_json(s3_client, bucket, f"{sample_prefix}upload_manifest.json", row)
        jsonl_rows.append(row)

    with local_output.open("w", encoding="utf-8") as fh:
        for row in jsonl_rows:
            fh.write(json.dumps(row, sort_keys=False, separators=(",", ":")) + "\n")

    shard_key = (
        f"manifests/raw/{dataset_version}/uploads/member={member}/run={run_id}.jsonl"
    )
    s3_client.upload_file(str(local_output), bucket, shard_key)

    print(
        f"Uploaded {len(jsonl_rows)} samples to s3://{bucket}/landing/raw/{dataset_version}/"
    )
    print(f"Wrote local shard: {local_output}")
    print(f"Uploaded shard: s3://{bucket}/{shard_key}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
