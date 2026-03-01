# NSFW Model Benchmarking

This directory contains code used for benchmarking candidate models to fine tune on.
This README outlines benchmarking models and the strategies used to test them. For developers, please refer to the [Developers](#Developers) section.

## Benchmarking Process

#### Overview 

1. Run the data pipeline and push to S3:
    1. **Render images**: Run OpenSCAD script to generate 6-8 views per 3D model → upload to S3
    2. **Generate text**: Use CLI tool to batch-generate synthetic user prompts via VLM API → upload to S3
    3. **Create manifests**: Build JSON manifest mapping model_id → [image_urls], text_prompt, ground_truth_label
2. **Run benchmarks**: Execute benchmark harness against each candidate VLM

### 1. Data Pipeline 

We use a curated, multi file per sample dataset of 3D models scraped from Thingiverse using the [Thingiverse Scraper](https://github.com/3D-Western/thingiscrape-clone). From this pool of data, we randomly sample a static 500 sample validation dataset as our benchmark dataset, with 30% `SAFE`, 40% `NSFW`, and 30% `GREY`, and from the raw 3D files we extract:

- Images using [OpenSCAD](https://github.com/HasNate618/OpenScad-STL-Renderer)
- Text generated via our data pipeline 

We push the data image and text pairs to a S3 bucket, see [Storage Strategy](#storage-strategy) for more details.

### 2. Benchmarking Pipeline + Strategy Overview 

We will not use Wandb for the time being and make assumptions on inference hyperparameters such as `do_sample`, `max_new_token`, `temperature`, and `torch_dtype` for floating point precisions, for standardized evaluation. However, this is subject to discussion. The benchmarking pipeline will:

- Run Python scripts using flags specified in a Bash script
- Pull data from S3 according to the flags 
- Batch requests and query remote API or run inference using the models on EC2.

Whether we do remote API calls or rent an EC2 instance to load model weights and benchmark is dependent on model availability and pricing. Subject to discussion.

Proposed/draft inference hyperparameters:

- `do_sample` = False to remove randomness in model output
- `temperature = 1.0 (or ignored)`: Since sampling is off, temperature doesn't affect the output, further reducing variables.
- `max_new_tokens`: Set a fixed limit to reduce cost and wordiness for easier evals.

NOTE: If a specific model's documentation explicitly states it requires a certain prompt template or a specific `repetition_penalty` to function correctly, we need to apply those model-specific requirements while keeping the core generation logic (greedy vs. sampling) the same.

**We will additionally test quantized versions of the same models listed below, to verify that quantization will fit our business needs equally well compared to the full precision counterpart.**

### Our Test Dataset

- Labeled 3D models: `SAFE`, `NSFW`, `GREY`
- 6-8 rendered views per model
- Edge cases: partial occlusions, ambiguous content, artistic models
- Ground truth from human moderators

We measure each model's candidancy via the following metrics:

| Metric | Description |
|--------|-------------|
| Accuracy | Correct classification rate vs ground truth |
| Precision | True positives / (True positives + False positives) |
| Recall | True positives / (True positives + False negatives) |
| F1 Score | Harmonic mean of precision and recall |
| Confidence Calibration | Does high confidence = high accuracy? |
| Inference Latency | Time per image batch |
| GPU Memory | VRAM usage at various precisions |
| Multi-view Consistency | Same label across different angles |

### Constraints: 

- The model must follow image + text as input and text as output.
- There is no hard cost ceiling, but the costs and operational overheads must be reasonable.
- Inference latency of 3s max excluding cold startup times for hosting on paid per usage platforms.

## Model Candidates  

### google/gemma-3-27b-it

| Specification | Value |
|---------------|-------|
| **Model Type** | Vision-Language Model (VLM) |
| **Input Modality** | Image + Text → Text |
| **Parameters** | 27B |
| **Primary Use Case** | Direct image classification for SFW/NSFW detection |
| **Context Window** | 128K tokens |
| **Architecture** | Gemma 3 with vision encoder |

**Capabilities:**
- Processes 1-2 key views of 3D rendered content
- Outputs direct classification labels: SAFE, GREY, or INAPPROPRIATE
- Provides reasoning for decisions
- Handles multi-image inputs within context window

**Benchmark Tasks:**
- Accuracy on labeled 3D model NSFW detection
- Confidence calibration (high confidence should correlate with correctness)
- Latency per inference on single/multi-image inputs
- GPU memory footprint (FP16, INT8, 4-bit quantized)
- False negative rate (NSFW detected as SAFE)
- False positive rate (SFW flagged as INAPPROPRIATE)

---

### Efficient-Large-Model/VILA-13b-4bit-awq

| Specification | Value |
|---------------|-------|
| **Model Type** | Vision-Language Model (VLM) |
| **Input Modality** | Multiple Images + Text → Text |
| **Parameters** | 13B |
| **Primary Use Case** | Confident multi-image classification |
| **Quantization** | 4-bit AWQ |
| **Architecture** | VILA (Vision Language Model) |

**Capabilities:**
- Processes ALL rendered views in single pass (multi-image)
- Designed for comprehensive 3D model analysis from multiple angles
- Lower memory footprint due to 4-bit quantization
- Outputs classification labels with reasoning

**Benchmark Tasks:**
- Accuracy vs single-image models on occluded/partial views
- Multi-view consistency (same classification across angles)
- Inference latency comparison (vs 27B models)
- Memory usage under 4-bit quantization
- Performance on edge cases (ambiguous poses, artistic content)

---

### Baseline: prometheus-eval/prometheus-vision-13b-v1.0

| Specification | Value |
|---------------|-------|
| **Model Type** | Vision-Language Model (VLM) |
| **Input Modality** | Image + Text → Text |
| **Parameters** | 13B |
| **Primary Use Case** | Evaluation baseline and comparison |
| **Architecture** | Vision-encoder + LLM |

**Benchmark Tasks:**
- Reference accuracy comparison for NSFW detection
- Agreement rate with other candidate models
- Calibration quality baseline

---

# Developers

This README provides an overview. More implementation details and instructions will be provided next week.

## Workflow Overview

```
Raw 3D Files (S3)
      ↓
[Data Pipeline: OpenSCAD Renderer + VLM Text Generation] → Image + Text Pairs (S3)
      ↓
[Benchmarking Pipeline] → Metrics & Reports
```

## Data Pipeline

### Storage Strategy

We will store all 3D files and the generated images and text pairs on S3. We will maintain three buckets/prefixes;
below is a proposed storage bucket format:

| Storage Location | Contents | Purpose |
|------------------|----------|---------|
| `s3://.../raw/` | Original .stl, .3mf, .obj files | Source of truth, reproducibility |
| `s3://.../processed/images/` | Rendered views (6-8 per model) | Cached to skip re-rendering |
| `s3://.../processed/text/` | Generated text prompts per model | Cached to skip re-generation |
| `s3://.../benchmarks/` | Final dataset manifests | Version-controlled test sets |

We can re-run benchmarks from cached processed data, after running the pipeline once. The data and code is separated,
and as long as our scripts take in the right args for the data source, every step is reproducible.

### Image -> Text (VLM) Generation Pipeline

1. Use GPT-4o, Claude, or a smaller local VLM to generate user-like descriptions.
2. Provide the rendered image + instructions to generate realistic user queries we would see in production.

Example prompt to text generator:

   ```
   "Given this 3D model render, generate 3 realistic user prompts
   describing what they want to print. Include casual language,
   typos, and varying levels of detail."
   ```

3. **CLI Automation**: We will make a bash script or Python CLI that:
   - Reads image paths from S3 or local directory
   - Batches requests to VLM API with rate limits taken into account 
   - Writes generated text back to S3 with metadata
   - Track progress to allow resumption on failure


Constraints:
- **Rate limiting**: Implement exponential backoff for API calls
- **Cost tracking**: ~1000 samples × 8 images × $0.005 per image = ~$40 for text generation
- **Validation**: We should spot-check generated text quality before a full benchmark run, or run incrementally
- **Versioning**: We must tag the processed datasets (e.g., `text-gen-v1-gpt4o`), and create a JSON to version the VLM used for generating the text for the dataset and the hyperparams used to also track which generation method was used

We will discuss and test VLM options and compile a cost report for the automated data pipeline.

Example script for testing VLMs:

```python
# generate_text.py
import asyncio
import boto3
from anthropic import AsyncAnthropic
import json

PROMPT_TEMPLATE = """
You are simulating a user submitting a 3D model for printing.
Look at these rendered images of a 3D model and write:
1. A natural user description similar to what someone would type when uploading
2. A title for the model
3. Any tags they might add

Be realistic. Users describe models informally.

Respond in JSON: {"title": "...", "description": "...", "tags": [...]}
"""

async def process_one(sample_id: str, image_paths: list[str], client: AsyncAnthropic):
    # Read images, encode as base64 or use URL if S3-hosted
    # Call API
    response = await client.messages.create(...)
    # Parse JSON, save to file
    return result

async def main():
    # Load sample IDs from S3 listing or local manifest
    # Process in batches with rate limiting
    # Save results locally and upload to S3
```

Before fully running a VLM to synthesize data for all of our data, we will do scoped tests for each for ~10-20 samples from the benchmark (500) dataset and evaluate the quality and closeness to expected input from production: 

```bash
# Generate 10 samples, human review
python generate_text.py --samples 10 --output validation_set/

# Check if text quality matches expectations
# Adjust prompt template if needed
```

Best Practices for remote AI calls: Check rate limits, and: 
- Add `asyncio.Semaphore(5)` to avoid rate limiting
- Add retry logic with exponential backoff

## Tech Stack

### Required Tools

| Component | Recommended | Notes |
|-----------|-------------|-------|
| Cloud Storage | AWS S3 | Stores raw 3D, images, text, results |
| Compute | AWS EC2 (g4dn.xlarge+) or RunPod | Likely going with EC2 |
| Container | Docker | Reproducible benchmarking environment + drivers config |
| Orchestration | Python + `typer` or `click` for CLI |
| VLM for text gen | TBD | Subj. to Discussion |
| Benchmarking | `HF transformers` or Via remote API call | Batch inference |
| Metrics | `scikit-learn`, custom eval scripts | Classification metrics |

### Draft CLI Structure

```bash
# Data pipeline commands
python pipeline.py render --source s3://bucket/raw/ --output s3://bucket/processed/images/
python pipeline.py generate-text --images s3://bucket/processed/images/ --output s3://bucket/processed/text/
python pipeline.py prepare-dataset --images s3://bucket/processed/images/ --text s3://bucket/processed/text/ --output manifest.json

# Benchmarking commands
python benchmark.py run --model google/gemma-3-27b-it --dataset manifest.json --report report.json
python benchmark.py report --results report.json --format markdown
```

### Environment Setup

We will be using `uv` for this project.

1. `cd` into the project and run `uv venv`. 
2. Activate the venv using `source .venv/bin/activate`.
3. Run `uv pip install -r requirements.txt` to install all dependencies.

To install a package, run `uv add <package_name>`. To remove a package, you can run `uv remove <package_name>`.

### Proposed Project Structure

```
benchmarking/
├── data/
│   ├── raw_3d/              # Source STL/OBJ files, gitignored.
│   ├── rendered_images/      # OpenSCAD output, gitignored.
│   │   └── {sample_id}/
│   │       ├── view_001.png
│   │       └── ...
│   └── synthetic_text/       # Generated text prompts, gitignored.
│       └── {sample_id}.json
├── scripts/
│   ├── render_3d.py          # OpenSCAD wrapper
│   ├── generate_text.py      # VLM text synthesis pipeline
│   ├── run_benchmark.py      # Main benchmark runner
│   └── evaluate_metrics.py   # Metrics computation
├── models/
│   └── .cache/               # Downloaded HF models (if any)
├── results/
│   └── {timestamp}/
│       ├── predictions.jsonl
│       └── metrics.json
├── tests/
│   └── test_validators.py
└── config.yaml               # Benchmark configuration
└── Dockerfile                # Dockerfile for EC2 
└── .venv                     # UV venv 
```

## Example Workflow for Running Benchmarks

### Step 1: Setup Environment

```bash
# Clone and setup
cd benchmarking/
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Download cached processed data
aws s3 sync s3://your-bucket/processed/ ./data/processed/
```

### Step 2: Generate Missing Text (if needed)

```bash
# If you need to expand dataset
export ANTHROPIC_API_KEY="..."
python scripts/generate_text.py \
    --sample-list new_samples.txt \
    --output-dir data/synthetic_text/

# Sync to S3
aws s3 sync data/synthetic_text/ s3://your-bucket/processed/text/
```

### Step 3: Download Candidate Models

```bash
# Models will cache to ~/.cache/huggingface/
python scripts/download_models.py \
    --models gemma-3-27b-it,VILA-13b-4bit-awq
```

### Step 4: Run Benchmark

```bash
# Single model
python scripts/run_benchmark.py \
    --model google/gemma-3-27b-it \
    --dataset config/benchmark_500.json \
    --output results/$(date +%Y%m%d_%H%M%S)/

# All candidates
python scripts/run_benchmark.py \
    --config config/candidates.yaml \
    --output results/$(date +%Y%m%d_%H%M%S)/
```

### Step 5: Compute Metrics

```bash
python scripts/evaluate_metrics.py \
    --predictions results/*/predictions.jsonl \
    --ground-truth config/benchmark_500_labels.json \
    --output results/metrics_report.json
```

## Example Data Pipeline Workflow: Text Synthesis

### 1. Environment Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure API keys and S3
export ANTHROPIC_API_KEY=...
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export S3_BUCKET=benchmarking-bucket
```

### 2. Generate Text Dataset

```bash
# Process all images in a directory
python scripts/generate_text.py \
    --images-s3-path s3://bucket/processed/v1/images/ \
    --output-s3-path s3://bucket/processed/v1/text_descriptions.jsonl \
    --provider anthropic \
    --model claude-3-5-sonnet-20241022
```

### 3. Run Benchmarks

```bash
# Single model evaluation
python benchmark.py \
    --model gemma-3-27b-it \
    --dataset s3://bucket/processed/v1/ \
    --metrics accuracy,precision,recall,latency \
    --output results/gemma-3-27b-it.json

# Compare all candidates
python benchmark.py --all-models --compare
```
