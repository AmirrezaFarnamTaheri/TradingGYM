import numpy as np
import logging

logger = logging.getLogger(__name__)


def _softmax(x: np.ndarray) -> np.ndarray:
    """Computes softmax activations along the last axis."""
    e_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
    return e_x / np.sum(e_x, axis=-1, keepdims=True)


def fuse_features(
    feature_vectors: np.ndarray,
    masks: np.ndarray,
) -> np.ndarray:
    """
    Calculates a weighted average of features from different timeframes using a dynamic attention mechanism.

    This function takes a tensor of feature vectors from different timeframes and computes a
    fused feature vector for each sample. The first feature vector in the tensor (along the first axis)
    is used as the query, while all feature vectors are used as keys and values.

    An optional mask can be provided to exclude certain timeframes from the attention calculation.

    Args:
        feature_vectors: A tensor of shape (n_timeframes, n_samples, n_features).
                         The first element along the first axis is treated as the base/query.
        masks: A boolean tensor of shape (n_timeframes, n_samples) corresponding to the feature vectors.

    Returns:
        The fused feature vectors as a tensor of shape (n_samples, n_features).
    """
    if feature_vectors.ndim != 3:
        raise ValueError(
            f"feature_vectors must be a 3D tensor, but got shape {feature_vectors.shape}"
        )
    if masks.ndim != 2:
        raise ValueError(f"masks must be a 2D tensor, but got shape {masks.shape}")

    n_timeframes, n_samples, n_features = feature_vectors.shape

    logger.debug(
        "Fusing features with %d timeframes for %d samples.", n_timeframes, n_samples
    )

    query = feature_vectors[0, :, :]
    keys = feature_vectors
    values = keys

    scores = np.einsum("ij,kij->ik", query, keys, dtype=np.float64)
    logger.debug("Attention scores (before mask): %s", scores.shape)

    mask = masks.T
    scores[mask] = -np.inf
    logger.debug("Attention scores (after mask): %s", scores.shape)

    attention_weights = _softmax(scores)
    logger.debug("Attention weights: %s", attention_weights.shape)

    context_vector = np.einsum("ik,kij->ij", attention_weights, values)
    logger.debug("Fused context vector: %s", context_vector.shape)

    return context_vector
