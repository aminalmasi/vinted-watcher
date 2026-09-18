#!/bin/bash
#SBATCH --job-name=vc-view
#SBATCH --partition=allgroups
#SBATCH --account=thesis
#SBATCH --gres=gpu:1
#SBATCH --exclude=dellcuda1
#SBATCH -c 4
#SBATCH --mem=64G
#SBATCH --time=2:00:00
#SBATCH --output=/home/malmasik/vestiaire_data/embeddings/view_%j.log
export HF_HOME=/extra/malmasik/hf_cache
/home/malmasik/.venvs/a100_new_real/bin/python /extra/malmasik/vinted/scripts/view_by_corpus.py
