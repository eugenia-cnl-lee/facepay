"""Download the LFW face dataset and turn it into a real enrolled-user
database plus a labelled test set.

Run once:  python build_dataset.py

Result:
    known/          enrolled people (you are kept as-is; LFW people added)
    test/genuine/   held-out photos of enrolled people  -> should be recognised
    test/stranger/  photos of people NOT enrolled        -> should be UNKNOWN
"""

from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from sklearn.datasets import fetch_lfw_people

# how big to make the dataset (keep modest so enrolment isn't slow)
NUM_ENROLLED_PEOPLE = 15       # LFW people added to known/ (the "citizens")
ENROLL_IMAGES_PER_PERSON = 3   # reference photos stored per enrolled person
GENUINE_TESTS_PER_PERSON = 2   # held-out photos of enrolled people, for testing
NUM_STRANGERS = 15             # people put ONLY in test/stranger (never enrolled)
STRANGER_IMAGES_PER_PERSON = 2

ENROLLED_DIR = Path("test") / "enrolled"
GENUINE_DIR = Path("test") / "genuine"
STRANGER_DIR = Path("test") / "stranger"


def save_image(rgb_image: np.ndarray, path: Path) -> None:
    """Save one LFW image (RGB, possibly 0-1 floats) as a normal .jpg file."""
    img = rgb_image
    if img.max() <= 1.0:               # sklearn scales pixels to 0-1; undo that
        img = img * 255.0
    img = img.astype(np.uint8)
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)   # OpenCV saves in BGR order
    cv2.imwrite(str(path), bgr)


def main() -> None:
    print("Downloading LFW (first run downloads ~200MB, then it's cached)...", flush=True)
    people = fetch_lfw_people(min_faces_per_person=15, color=True, resize=1.0)

    images = people.images          # shape: (n, height, width, 3)
    labels = people.target          # integer id per image
    names = people.target_names     # id -> "George W Bush"

    # group every image index by which person it belongs to
    by_person: dict[int, list[int]] = defaultdict(list)
    for index, label in enumerate(labels):
        by_person[label].append(index)

    # people with the most photos first (so we can split enrol vs test cleanly)
    people_by_photo_count = sorted(by_person, key=lambda p: -len(by_person[p]))
    enrolled_ids = people_by_photo_count[:NUM_ENROLLED_PEOPLE]
    stranger_ids = people_by_photo_count[
        NUM_ENROLLED_PEOPLE:NUM_ENROLLED_PEOPLE + NUM_STRANGERS
    ]

    ENROLLED_DIR.mkdir(parents=True, exist_ok=True)
    GENUINE_DIR.mkdir(parents=True, exist_ok=True)
    STRANGER_DIR.mkdir(parents=True, exist_ok=True)

    # enrolled people: reference photos -> test/enrolled/, held-out -> test/genuine/
    for label in enrolled_ids:
        name = names[label].replace(" ", "_")
        indices = by_person[label]

        enrol_indices = indices[:ENROLL_IMAGES_PER_PERSON]
        genuine_indices = indices[
            ENROLL_IMAGES_PER_PERSON:
            ENROLL_IMAGES_PER_PERSON + GENUINE_TESTS_PER_PERSON
        ]

        for i, index in enumerate(enrol_indices, start=1):
            save_image(images[index], ENROLLED_DIR / f"{name}_{i}.jpg")
        for i, index in enumerate(genuine_indices, start=1):
            save_image(images[index], GENUINE_DIR / f"{name}_{i}.jpg")

    # strangers: a couple of photos each, ONLY into test/stranger/
    for label in stranger_ids:
        name = names[label].replace(" ", "_")
        indices = by_person[label][:STRANGER_IMAGES_PER_PERSON]
        for i, index in enumerate(indices, start=1):
            save_image(images[index], STRANGER_DIR / f"{name}_{i}.jpg")

    print("Done. (LFW is for evaluation only — it never touches known/.)")
    print(f"  test/enrolled/ {NUM_ENROLLED_PEOPLE} LFW people (evaluation gallery)")
    print(f"  test/genuine/  held-out photos of enrolled people")
    print(f"  test/stranger/ {NUM_STRANGERS} people who are NOT enrolled")


if __name__ == "__main__":
    main()
