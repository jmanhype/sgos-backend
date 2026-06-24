"""
Voice Match Scorer — Scores variants by similarity to user's voice profile.

Uses TF-IDF cosine similarity (fast, no LLM needed).
Compares variant content against voice profile samples.

SOLID:
  - Single Responsibility: Only evaluates voice similarity.
  - Open/Closed: Implements IVariantScorer protocol.
  - Liskov: Drop-in replacement for any scorer.
"""
import json
import math
import re
from collections import Counter

from services.pipeline.protocols import ViralGenome, ContentVariant
from database import get_connection


class VoiceMatchScorer:
    """
    Scores variants by how well they match the user's writing voice.
    
    Uses TF-IDF cosine similarity between the variant and the voice
    profile's raw samples. Score range: 0-100.
    
    Falls back to neutral score (50) if no voice profile exists.
    """

    def __init__(self, weight: float = 0.3, profile_name: str = "default"):
        self._weight = weight
        self._profile_name = profile_name
        self._voice_tfidf: dict[str, float] | None = None
        self._voice_norm: float = 0.0
        self._loaded = False

    @property
    def name(self) -> str:
        return "voice_match"

    @property
    def weight(self) -> float:
        return self._weight

    def _ensure_loaded(self):
        """Lazy-load voice profile on first score call."""
        if self._loaded:
            return
        
        self._loaded = True
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT raw_samples, common_words FROM voice_profiles WHERE name = ?",
                (self._profile_name,)
            ).fetchone()
            
            if not row:
                return
            
            samples = json.loads(row["raw_samples"]) if row["raw_samples"] else []
            if not samples:
                return
            
            # Build a combined TF-IDF vector from all voice samples
            combined = " ".join(samples)
            self._voice_tfidf = self._build_tfidf(combined)
            self._voice_norm = self._vector_norm(self._voice_tfidf)
            
        except Exception:
            pass

    def score(self, variant: ContentVariant, genome: ViralGenome) -> float:
        """Score voice similarity (0-100). 50 = neutral if no profile."""
        self._ensure_loaded()
        
        if not self._voice_tfidf or self._voice_norm == 0:
            return 50.0  # Neutral — no profile to compare against
        
        variant_tfidf = self._build_tfidf(variant.content)
        variant_norm = self._vector_norm(variant_tfidf)
        
        if variant_norm == 0:
            return 0.0
        
        cosine = self._cosine_similarity(
            self._voice_tfidf, variant_tfidf,
            self._voice_norm, variant_norm
        )
        
        # Cosine similarity is 0-1, scale to 0-100 with amplification
        # (most content will have some overlap, so we stretch the range)
        return min(cosine * 150, 100)  # Amplify: 0.67 cosine → 100

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize text into lowercase words."""
        return re.findall(r'\b[a-z]{3,}\b', text.lower())

    @staticmethod
    def _build_tfidf(text: str) -> dict[str, float]:
        """Build a simple TF-IDF vector from text."""
        tokens = VoiceMatchScorer._tokenize(text)
        if not tokens:
            return {}
        
        # Term frequency (log-normalized)
        counts = Counter(tokens)
        total = len(tokens)
        tf = {word: (1 + math.log(count / total)) for word, count in counts.items() if count > 0}
        
        return tf

    @staticmethod
    def _vector_norm(vec: dict[str, float]) -> float:
        """L2 norm of a sparse vector."""
        return math.sqrt(sum(v * v for v in vec.values()))

    @staticmethod
    def _cosine_similarity(
        vec_a: dict[str, float], vec_b: dict[str, float],
        norm_a: float, norm_b: float
    ) -> float:
        """Cosine similarity between two sparse vectors."""
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        # Dot product (only iterate over smaller set)
        smaller, larger = (vec_a, vec_b) if len(vec_a) <= len(vec_b) else (vec_b, vec_a)
        dot = sum(smaller[k] * larger[k] for k in smaller if k in larger)
        
        return dot / (norm_a * norm_b)
