"""The label schema (D85, D86): the one module the labeling tool, the loader and S3's tagger share.

Nothing here touches a file, a database or message text.
"""

from dataclasses import dataclass

# D85: exactly one topic and exactly one sentiment per message. The order is the order of the
# number keys in the labeling tool (1-9, then 0 for the tenth), so never reorder it casually.
TOPICS = (
    "plans",
    "school",
    "work",
    "food",
    "entertainment",
    "sports_fitness",
    "relationships",
    "banter",
    "news",
    "other",
)
SENTIMENTS = ("positive", "neutral", "negative")

# D85: the tagger and the labeler see a message plus up to this many previous eligible messages
# of the same thread.
TAG_CONTEXT_MESSAGES = 2
# D86: the question flow shows up to this many eligible messages on each side of the shown one.
QUESTION_CONTEXT_EACH_SIDE = 3

# D86: sample sizes, the seed, and the per-thread floor of the stratified draw.
TAG_SAMPLE_SIZE = 200
QUESTION_TARGET = 100
# The pool of candidates is larger than the target because some messages cannot make a question.
QUESTION_CANDIDATE_POOL = 3 * QUESTION_TARGET
THREAD_FLOOR = 5
SAMPLE_SEED = 20261110

# Fixed CSV schemas (D86). These are also the headers of the committed synthetic sets.
TAG_HEADER = ("message_id", "topic", "sentiment", "labeled_at")
QUESTION_HEADER = (
    "question_id",
    "question",
    "gold_message_ids",
    "source_message_id",
    "labeled_at",
)
# Candidates Ryan skipped, so that resuming does not show them again. Not an eval file.
SKIP_HEADER = ("message_id", "skipped_at")
GOLD_SEPARATOR = ";"

# File names inside GCW_LABEL_DIR.
TAG_LABELS_FILE = "tag_labels.csv"
QUESTIONS_FILE = "questions.csv"
SKIPS_FILE = "question_skips.csv"
TAG_SAMPLE_FILE = "tag_sample.json"
QUESTION_CANDIDATES_FILE = "question_candidates.json"


@dataclass(frozen=True)
class TagLabel:
    message_id: str
    topic: str
    sentiment: str
    labeled_at: str  # ISO 8601 with a UTC offset


@dataclass(frozen=True)
class QuestionLabel:
    question_id: str
    question: str
    gold_message_ids: tuple[str, ...]  # chronological; includes source_message_id
    source_message_id: str
    labeled_at: str


@dataclass(frozen=True)
class Skip:
    message_id: str
    skipped_at: str
