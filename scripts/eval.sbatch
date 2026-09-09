#!/bin/bash
#SBATCH --job-name=vc-eval
#SBATCH --partition=allgroups
#SBATCH --account=thesis
#SBATCH --gres=gpu:1
#SBATCH -c 4
#SBATCH --mem=48G
#SBATCH --time=1:00:00
#SBATCH --output=/home/malmasik/vestiaire_data/embeddings/eval_%j.log
export HF_HOME=/extra/malmasik/hf_cache
/home/malmasik/.venvs/a100_new_real/bin/python /extra/malmasik/vinted/scripts/eval_retrieval.py
