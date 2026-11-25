"""The registry of pinned models (D90, D93) and the checksum-verified download for llm-model.

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

# D98. MODEL_ID is what enrich.message_tags.model_id records and what the server is told to call
# itself; the compose default for the file name must equal MODEL_FILE (a test checks it).
MODEL_ID = "qwen3.5-4b-q4_k_m"
MODEL_FILE = "Qwen3.5-4B-Q4_K_M.gguf"
MODEL_SHA256 = "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4"
MODEL_BYTES = 2_740_937_888
MODEL_URL = (
    "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/"
    "e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf"
)
DEFAULT_MODEL_DIR = "~/.cache/gcw/models"
CHUNK_BYTES = 1 << 20
DOWNLOAD_TIMEOUT_SECONDS = 60


class ChecksumMismatchError(Exception):
    """The downloaded or existing file does not match the pinned sha256."""


@dataclass(frozen=True)
class ModelSpec:
    """One pinned model. `enable_thinking` is the per-model request setting (D98): None sends
    nothing (the model has no thinking mode); False sends chat_template_kwargs enable_thinking
    false on every request. It comes from here, never from matching the model's name."""

    file: str = MODEL_FILE
    sha256: str = MODEL_SHA256
    url: str = MODEL_URL
    enable_thinking: bool | None = False

    @property
    def chat_template_kwargs(self) -> dict[str, bool] | None:
        if self.enable_thinking is None:
            return None
        return {"enable_thinking": self.enable_thinking}


# D98 arm B (Apache-2.0). The URL names an exact Hugging Face commit; sha256 is the LFS object id.
SMALL_MODEL_ID = "qwen3.5-2b-q4_k_m"
SMALL_MODEL_SPEC = ModelSpec(
    file="Qwen3.5-2B-Q4_K_M.gguf",
    sha256="aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223",
    url=(
        "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/"
        "f6d5376be1edb4d416d56da11e5397a961aca8ae/Qwen3.5-2B-Q4_K_M.gguf"
    ),
)

# D100: the smallest Qwen3.5 (Apache-2.0), used only by the CI eval gate on free runners. Same
# family and thinking-off setting as the arms, so the production prompt and client run unchanged.
# Never an experiment arm and never promoted. The URL names an exact commit; sha256 is the LFS id.
TINY_MODEL_ID = "qwen3.5-0.8b-q4_k_m"
TINY_MODEL_SPEC = ModelSpec(
    file="Qwen3.5-0.8B-Q4_K_M.gguf",
    sha256="bd258782e35f7f458f8aced1adc053e6e92e89bc735ba3be89d38a06121dc517",
    url=(
        "https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/resolve/"
        "6ab461498e2023f6e3c1baea90a8f0fe38ab64d0/Qwen3.5-0.8B-Q4_K_M.gguf"
    ),
)

# D90 model, kept as a reference (D98): evaluated with tag-eval for the record, never an arm. It
# has no thinking mode, so its requests carry no template setting (and keep its old prompt version).
REFERENCE_MODEL_ID = "qwen3-4b-instruct-2507-q4_k_m"
REFERENCE_MODEL_SPEC = ModelSpec(
    file="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    sha256="3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597",
    url=(
        "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/"
        "a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    ),
    enable_thinking=None,
)

# model id -> file, sha256, url. The id is what the llm service's --alias, the client's request and
# enrich.message_tags.model_id all carry. Only registered models can be fetched, served or promoted.
REGISTRY: dict[str, ModelSpec] = {
    MODEL_ID: ModelSpec(),
    SMALL_MODEL_ID: SMALL_MODEL_SPEC,
    TINY_MODEL_ID: TINY_MODEL_SPEC,
    REFERENCE_MODEL_ID: REFERENCE_MODEL_SPEC,
}


# D102: the embedding model (official Qwen GGUF, Apache-2.0; exact commit, sha256 is the LFS id).
# A sibling registry, not part of REGISTRY, so no tagger command can serve, compare or promote it.
EMBED_MODEL_ID = "qwen3-embedding-0.6b-q8_0"
EMBED_MODEL_SPEC = ModelSpec(
    file="Qwen3-Embedding-0.6B-Q8_0.gguf",
    sha256="06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439",
    url=(
        "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/"
        "370f27d7550e0def9b39c1f16d3fbaa13aa67728/Qwen3-Embedding-0.6B-Q8_0.gguf"
    ),
    enable_thinking=None,  # an embedding model has no chat template to configure
)
EMBED_REGISTRY: dict[str, ModelSpec] = {EMBED_MODEL_ID: EMBED_MODEL_SPEC}


class UnknownModelError(ValueError):
    """The model id is not registered. The message omits the id."""


def spec_for(model_id: str) -> ModelSpec:
    try:
        return REGISTRY[model_id]
    except KeyError:
        raise UnknownModelError("model id is not registered") from None


def downloadable_spec(model_id: str) -> ModelSpec:
    """The pinned file behind any registered model, tagger or embedding (`make llm-model`)."""
    return EMBED_REGISTRY.get(model_id) or spec_for(model_id)


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
