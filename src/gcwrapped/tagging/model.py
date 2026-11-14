"""The pinned default model (D90) and the checksum-verified download behind `make llm-model`.

The file lives outside the repo (default ~/.cache/gcw/models) and is mounted read-only into the
llm compose service. The URL names an exact Hugging Face commit, and the sha256 is checked before
the file gets its final name, so a changed or truncated download is never served. Downloading the
model sends no chat data anywhere: the request is a plain GET of a public file.
"""

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from gcwrapped.config import TaggingSettings

# D90. MODEL_ID is what enrich.message_tags.model_id records and what the server is told to call
# itself; the compose default for the file name must equal MODEL_FILE (a test checks it).
MODEL_ID = "qwen3-4b-instruct-2507-q4_k_m"
MODEL_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
MODEL_SHA256 = "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597"
MODEL_BYTES = 2_497_281_120
MODEL_URL = (
    "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/"
    "a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
)
DEFAULT_MODEL_DIR = "~/.cache/gcw/models"
CHUNK_BYTES = 1 << 20
DOWNLOAD_TIMEOUT_SECONDS = 60


class ChecksumMismatchError(Exception):
    """The downloaded or existing file does not match the pinned sha256."""


@dataclass(frozen=True)
class ModelSpec:
    file: str = MODEL_FILE
    sha256: str = MODEL_SHA256
    url: str = MODEL_URL


def model_dir() -> Path:
    """GCW_MODEL_DIR if set, else ~/.cache/gcw/models, with ~ expanded."""
    configured = TaggingSettings().gcw_model_dir or DEFAULT_MODEL_DIR
    return Path(configured).expanduser()


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_model(directory: Path, spec: ModelSpec | None = None) -> bool:
    """Make `directory/spec.file` exist with the pinned checksum. True if it was downloaded.

    Idempotent: a present file with the right sha256 is left alone. A present file with the wrong
    one is an error, not something to overwrite silently. A download goes to a temp name first
    and is renamed only after its sha256 matches.
    """
    spec = spec or ModelSpec()
    if not spec.url.startswith("https://") and not spec.url.startswith("http://127.0.0.1"):
        raise ValueError("model URL must be https")  # the loopback form exists for tests only
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / spec.file
    if target.exists():
        if sha256_of(target) != spec.sha256:
            raise ChecksumMismatchError
        return False
    partial = directory / f".{spec.file}.part"
    try:
        digest = hashlib.sha256()
        with (
            urllib.request.urlopen(spec.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response,
            partial.open("wb") as out,
        ):
            while chunk := response.read(CHUNK_BYTES):
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if digest.hexdigest() != spec.sha256:
            raise ChecksumMismatchError
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return True
