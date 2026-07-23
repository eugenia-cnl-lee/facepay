from pathlib import Path

import numpy as np
from deepface import DeepFace


MODEL_NAME = "Facenet512"
DISTANCE_THRESHOLD = 0.30    # below this = same person
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
        embedding = create_embedding(image_path)

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


def main() -> None:
    database = enrol_faces()
    name, distance = identify_face(Path("test.jpg"), database)

    print(f"\nResult: {name}")
    print(f"Cosine distance: {distance:.3f}")


if __name__ == "__main__":
    main()