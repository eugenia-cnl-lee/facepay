import logging_setup  # noqa: F401  — MUST precede the deepface import; silences TF logs

from pathlib import Path

import cv2
import numpy as np
from deepface import DeepFace

MODEL_NAME = "Facenet512"
DISTANCE_THRESHOLD = 0.25    # evaluation-derived: loosest point with 0% false accepts (DECISIONS.md D10)
KNOWN_FACES_DIRECTORY = Path("known")
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def create_embedding(image_path: Path) -> np.ndarray:
    """Create one FaceNet512 embedding from an image containing one face."""
    results = DeepFace.represent(
        img_path=str(image_path),
        model_name=MODEL_NAME,
        detector_backend="opencv",
        enforce_detection=True,
    )

    if len(results) != 1:
        raise ValueError(
            f"{image_path} must contain exactly one detectable face."
        )

    return np.asarray(results[0]["embedding"], dtype=np.float32)


def calculate_cosine_distance(
    first_embedding: np.ndarray,
    second_embedding: np.ndarray,
) -> float:
    """Return the cosine distance between two face embeddings."""
    denominator = (
        np.linalg.norm(first_embedding)
        * np.linalg.norm(second_embedding)
    )

    if denominator == 0:
        raise ValueError("A face embedding unexpectedly had zero length.")

    similarity = np.dot(first_embedding, second_embedding) / denominator
    return float(1 - similarity)


def enrol_faces(
    directory: Path = KNOWN_FACES_DIRECTORY,
) -> dict[str, list[np.ndarray]]:
    """Create an in-memory embedding database from labelled photographs."""
    if not directory.exists():
        raise FileNotFoundError(
            f"Create the directory '{directory}' and add enrolment photos."
        )

    database: dict[str, list[np.ndarray]] = {}

    for image_path in sorted(directory.iterdir()):
        if image_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        person_name = image_path.stem.rsplit("_", maxsplit=1)[0]

        try:
            embedding = create_embedding(image_path)
        except ValueError as error:
            print(f"Skipped {image_path.name}: {error}")
            continue

        database.setdefault(person_name, []).append(embedding)
        print(f"Enrolled {image_path.name} as {person_name}")

    if not database:
        raise ValueError("No supported enrolment images were found.")

    return database


def identify_face(
    image_path: Path,
    database: dict[str, list[np.ndarray]],
) -> tuple[str, float]:
    """Return the closest accepted identity or UNKNOWN."""
    query_embedding = create_embedding(image_path)

    best_person = "UNKNOWN"
    best_distance = float("inf")

    for person_name, embeddings in database.items():
        for enrolled_embedding in embeddings:
            distance = calculate_cosine_distance(
                query_embedding,
                enrolled_embedding,
            )

            if distance < best_distance:
                best_person = person_name
                best_distance = distance

    if best_distance >= DISTANCE_THRESHOLD:
        return "UNKNOWN", best_distance

    return best_person, best_distance


def capture_from_webcam(save_path: Path = Path("scan.jpg")) -> Path:
    """Open the webcam, let the user capture one frame, and save it."""
    camera = cv2.VideoCapture(0)          # 0 = default webcam
    if not camera.isOpened():
        raise RuntimeError("Could not open the webcam.")

    print("Look at the camera. Press SPACE to scan, ESC to cancel.")
    captured_path = None

    while True:
        ok, frame = camera.read()
        if not ok:
            break

        mirrored = cv2.flip(frame, 1)     # flip so the preview acts like a mirror
        cv2.imshow("FacePay scan - SPACE to capture", mirrored)
        key = cv2.waitKey(1) & 0xFF

        if key == 27:                     # ESC
            break
        if key == 32:                     # SPACE
            cv2.imwrite(str(save_path), frame)
            captured_path = save_path
            break

    camera.release()
    cv2.destroyAllWindows()

    if captured_path is None:
        raise RuntimeError("No frame was captured.")
    return captured_path


def main() -> None:
    print(">>> [1] main() started", flush=True)

    print(">>> [2] enrolling known faces (this loads the model, may take a bit)...", flush=True)
    database = enrol_faces()
    print(f">>> [3] enrolment done, {len(database)} person(s) in database", flush=True)

    print(">>> [4] opening webcam...", flush=True)
    scan_path = capture_from_webcam()          # ← live face, not a file
    print(">>> [5] scan captured, identifying...", flush=True)
    name, distance = identify_face(scan_path, database)

    print(f"\nResult: {name}")
    print(f"Cosine distance: {distance:.3f}")


if __name__ == "__main__":
    main()