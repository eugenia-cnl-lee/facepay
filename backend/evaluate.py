"""Evaluate recognition accuracy across a range of thresholds.

Run:  python evaluate.py
Needs the test set produced by build_dataset.py (test/genuine, test/stranger).
"""

import logging_setup  # noqa: F401  — MUST precede the deepface import; silences TF logs

from pathlib import Path

from recognition import calculate_cosine_distance, create_embedding, enrol_faces


#  Configuration

ENROLLED_DIR = Path("test") / "enrolled"
GENUINE_DIR = Path("test") / "genuine"
STRANGER_DIR = Path("test") / "stranger"
THRESHOLDS = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


# Matching

def nearest_match(embedding, database):
    best_person = "UNKNOWN"
    best_distance = float("inf")

    for person_name, embeddings in database.items():
        for enrolled_embedding in embeddings:
            distance = calculate_cosine_distance(embedding, enrolled_embedding)
            if distance < best_distance:
                best_person = person_name
                best_distance = distance

    return best_person, best_distance


# Scoring

def score_directory(directory, database):
    results = []

    if not directory.exists():
        return results

    for image_path in sorted(directory.iterdir()):
        if image_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        try:
            embedding = create_embedding(image_path)
        except ValueError:
            continue

        true_person = image_path.stem.rsplit("_", maxsplit=1)[0]
        matched_person, distance = nearest_match(embedding, database)
        results.append((true_person, matched_person, distance))

    return results


# Report

def main():
    database = enrol_faces(ENROLLED_DIR)

    genuine = score_directory(GENUINE_DIR, database)
    strangers = score_directory(STRANGER_DIR, database)

    if not genuine and not strangers:
        raise SystemExit("No test images found. Run build_dataset.py first.")

    print(f"\nGenuine test images:  {len(genuine)}")
    print(f"Stranger test images: {len(strangers)}\n")

    header = f"{'Threshold':>10} {'False reject':>18} {'False accept':>18} {'Misident':>10}"
    print(header)
    print("-" * len(header))

    for threshold in THRESHOLDS:
        false_rejects = sum(1 for _, _, d in genuine if d >= threshold)
        misident = sum(
            1 for true_p, matched_p, d in genuine
            if d < threshold and matched_p != true_p
        )
        false_accepts = sum(1 for _, _, d in strangers if d < threshold)

        frr = false_rejects / len(genuine) if genuine else 0.0
        far = false_accepts / len(strangers) if strangers else 0.0

        reject_col = f"{false_rejects}/{len(genuine)} ({frr:.0%})"
        accept_col = f"{false_accepts}/{len(strangers)} ({far:.0%})"
        print(f"{threshold:>10.2f} {reject_col:>18} {accept_col:>18} {misident:>10}")


if __name__ == "__main__":
    main()
