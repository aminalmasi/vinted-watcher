#!/bin/bash
#SBATCH --job-name=vc-embed
#SBATCH --partition=allgroups
#SBATCH --account=thesis
#SBATCH --gres=gpu:1
#SBATCH -c 8
#SBATCH --mem=48G
#SBATCH --time=6:00:00
#SBATCH --output=/home/malmasik/vestiaire_data/embeddings/embed_%j.log
export HF_HOME=/extra/malmasik/hf_cache
export TMPDIR=$HOME/tmp
/home/malmasik/.venvs/a100_new_real/bin/python \
  /extra/malmasik/vinted/scripts/embed_corpus.py
