# Verified packaging image dataset

The application has no verified reference dataset, so model training and anomaly
inference are not enabled. Do not populate this structure with generated,
unverified, or merely assumed labels.

Prepare locally curated, permission-cleared images:

```text
ml/dataset/verified/
  genuine/
    <package-or-lot-id>_<view>.jpg
  anomalous/
    <package-or-lot-id>_<view>.jpg
```

Each label must be supported by a documented, independent verification process.
Keep all views of one package/lot in the same class. Use an identifier without
underscores before the first underscore in each filename, or use one directory
per package; the baseline groups train/holdout splits using these identifiers
to avoid placing package views in both sets.

The HOG + linear SVM script is an evaluation scaffold, not a counterfeit
detector. It prints a group-held-out confusion matrix, precision, recall and F1
score for the anomalous class, and writes model metadata. Small, biased or
non-representative datasets cannot establish real-world performance. The Flask
inspection workflow deliberately does not load or use the saved model.

Training example, after preparing a genuinely verified dataset:

```powershell
python ml\train_baseline.py --dataset-root ml\dataset\verified --output-dir ml\models\local
```

Model files and local dataset images should not be committed unless project
owners have reviewed their provenance, permissions and evaluation results.
