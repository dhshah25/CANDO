#!/bin/bash -l
#SBATCH --clusters=faculty
#SBATCH --partition=compbio
#SBATCH --qos=compbio
#SBATCH --account=rams
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=180G
#SBATCH --gpus-per-node=1
#SBATCH --constraint=A100
#SBATCH --time=02:00:00
#SBATCH --job-name=oss120b
#SBATCH --output=/projects/academic/rams/dshah22/logs/%j.out
#SBATCH --error=/projects/academic/rams/dshah22/logs/%j.err

# --- diagnostics -------------------------------------------------------
echo "job $SLURM_JOB_ID on $(hostname)"
echo "started $(date)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

# --- run ---------------------------------------------------------------
PY=/projects/academic/rams/wmangion/gpt-oss/bin/python
cd /projects/academic/rams/dshah22

$PY run_batch.py

echo "ended $(date)"
