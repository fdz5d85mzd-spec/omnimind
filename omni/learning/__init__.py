"""Learning & Evaluation Pipeline — every task becomes training data."""

from omni.learning.aggregate import METRIC_FIELDS, aggregate, trends
from omni.learning.pipeline import LearningPipeline
from omni.learning.store import EvaluationStore

__all__ = ["LearningPipeline", "EvaluationStore", "aggregate", "trends", "METRIC_FIELDS"]
