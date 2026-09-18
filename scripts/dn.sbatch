#!/bin/bash
#SBATCH --job-name=vc-dn
#SBATCH --partition=allgroups
#SBATCH --account=thesis
#SBATCH --gres=gpu:1
#SBATCH --exclude=dellcuda1
#SBATCH -c 4
#SBATCH --mem=64G
#SBATCH --time=1:00:00
#SBATCH --output=/home/malmasik/vestiaire_data/embeddings/dn_%j.log
/home/malmasik/.venvs/a100_new_real/bin/python /extra/malmasik/vinted/scripts/diag_photoidx.py
