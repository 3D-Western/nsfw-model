
# Installation
1. Clone the repository
    git clone 

2. Create a Python virtual environment
    python -m venv .venv 
    source .venv/bin/activate

3. Install dependencies
    pip install -r requirements.txt

# Configuration
Models and training parameters are defined in the configs folder.

# Running Fine-tuning 
To run the full pipeline:
    bash run_finetuning.sh

This script performs the following steps:
1. Prepare the dataset
2. Load the selected model
3. Fine-tune the model
4. Evaluate performance