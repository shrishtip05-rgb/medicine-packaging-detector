"""Train and evaluate a small HOG/SVM baseline on locally verified images."""

import argparse
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
CLASS_NAMES = ("genuine", "anomalous")
IMAGE_SIZE = (128, 128)


def load_images(dataset_root):
    examples = []
    for label, class_name in enumerate(CLASS_NAMES):
        class_dir = dataset_root / class_name
        if not class_dir.is_dir():
            raise ValueError(f"Missing class directory: {class_dir}")
        paths = sorted(
            path
            for path in class_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )
        if len(paths) < 2:
            raise ValueError(
                f"At least two images are required for class '{class_name}' "
                "to create train and holdout sets."
            )
        examples.extend(
            (path, label, _package_group(path, class_dir)) for path in paths
        )
    return examples


def _package_group(path, class_dir):
    relative = path.relative_to(class_dir)
    return str(relative.parent / relative.stem.split("_", 1)[0])


def extract_feature(path, hog):
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Could not decode image: {path}")
    resized = cv2.resize(image, IMAGE_SIZE, interpolation=cv2.INTER_AREA)
    return hog.compute(resized).reshape(-1)


def grouped_split(examples, seed):
    """Make a deterministic stratified holdout split without shared package groups."""
    randomizer = random.Random(seed)
    training = []
    holdout = []
    for label in (0, 1):
        grouped_examples = {}
        for example in examples:
            if example[1] == label:
                grouped_examples.setdefault(example[2], []).append(example)
        group_names = list(grouped_examples)
        if len(group_names) < 2:
            raise ValueError(
                f"At least two distinct package groups are required for "
                f"class '{CLASS_NAMES[label]}'. Use <package-id>_<view>.jpg "
                "filenames or one directory per package."
            )
        randomizer.shuffle(group_names)
        holdout_count = max(1, round(len(group_names) * 0.2))
        if holdout_count >= len(group_names):
            holdout_count = len(group_names) - 1
        holdout_groups = set(group_names[:holdout_count])
        for group_name, group_examples in grouped_examples.items():
            target = holdout if group_name in holdout_groups else training
            target.extend(group_examples)
    randomizer.shuffle(training)
    randomizer.shuffle(holdout)
    return training, holdout


def classification_metrics(actual, predicted):
    true_negative = sum(a == 0 and p == 0 for a, p in zip(actual, predicted))
    false_positive = sum(a == 0 and p == 1 for a, p in zip(actual, predicted))
    false_negative = sum(a == 1 and p == 0 for a, p in zip(actual, predicted))
    true_positive = sum(a == 1 and p == 1 for a, p in zip(actual, predicted))
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    total = len(actual)
    accuracy = (true_positive + true_negative) / total if total else 0.0
    return {
        "labels": ["genuine", "anomalous"],
        "confusion_matrix": [[true_negative, false_positive], [false_negative, true_positive]],
        "anomalous_precision": precision,
        "anomalous_recall": recall,
        "anomalous_f1": f1,
        "accuracy": accuracy,
        "holdout_count": total,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=23)
    args = parser.parse_args()

    try:
        _train_and_evaluate(args)
    except (OSError, ValueError, RuntimeError, cv2.error) as exc:
        parser.error(str(exc))


def _train_and_evaluate(args):
    examples = load_images(args.dataset_root)
    training, holdout = grouped_split(examples, args.seed)
    hog = cv2.HOGDescriptor(
        IMAGE_SIZE,
        (16, 16),
        (8, 8),
        (8, 8),
        9,
    )
    train_features = np.asarray(
        [extract_feature(path, hog) for path, _label, _group in training], dtype=np.float32
    )
    train_labels = np.asarray(
        [label for _path, label, _group in training], dtype=np.int32
    )
    holdout_features = np.asarray(
        [extract_feature(path, hog) for path, _label, _group in holdout],
        dtype=np.float32,
    )
    holdout_labels = [label for _path, label, _group in holdout]

    model = cv2.ml.SVM_create()
    model.setType(cv2.ml.SVM_C_SVC)
    model.setKernel(cv2.ml.SVM_LINEAR)
    model.setC(1.0)
    if not model.train(train_features, cv2.ml.ROW_SAMPLE, train_labels):
        raise RuntimeError("The baseline classifier could not be trained.")
    _unused, raw_predictions = model.predict(holdout_features)
    predictions = [int(value) for value in raw_predictions.ravel()]
    metrics = classification_metrics(holdout_labels, predictions)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model_path = args.output_dir / "packaging_hog_svm.xml"
    model.save(str(model_path))
    dataset_digest = hashlib.sha256(
        "\n".join(
            f"{path.relative_to(args.dataset_root)}:{label}"
            for path, label, _group in sorted(examples)
        ).encode("utf-8")
    ).hexdigest()
    metadata = {
        "model_type": "HOG + linear SVM",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "opencv_version": cv2.__version__,
        "seed": args.seed,
        "train_count": len(training),
        "holdout_count": len(holdout),
        "train_package_groups": len({group for _path, _label, group in training}),
        "holdout_package_groups": len({group for _path, _label, group in holdout}),
        "dataset_manifest_sha256": dataset_digest,
        "metrics": metrics,
        "limitations": [
            "Metrics describe only the supplied dataset and do not establish real-world counterfeit-detection performance.",
            "No verified dataset is included, and this model is not integrated into Flask inference.",
        ],
    }
    metadata_path = args.output_dir / "packaging_hog_svm.metrics.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps({"model_path": str(model_path), "metadata_path": str(metadata_path), **metrics}, indent=2))


if __name__ == "__main__":
    main()
