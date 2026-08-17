"""
Grounding Verifier — Self-correcting hallucination detection.

After the LLM generates a draft, this module:
1. Extracts every specific factual claim (product names, numbers, features, quotes)
2. Checks each claim against the source material
3. Strips or flags unsourced claims
4. Returns a grounding score (0-100%) and corrected content

Architecture:
  - Uses a STRICT verification LLM pass (different prompt framing — adversarial reviewer)
  - Distinguishes: sourced claims ✅ | general knowledge ✅ | fabricated specifics ❌
  - Auto-retry: if grounding < 80%, regenerate with stronger constraints
  - Visible in dashboard: every draft shows its grounding score

This is the layer that prevents "Newell Nucleus" hallucinations.
"""
import json
import re


# ─── Claim Extraction ───────────────────────────────────────────────────────

SPECIFIC_CLAIM_PATTERNS = [
    # Product/feature names in quotes or capitalized mid-sentence
    r'"([^"]{3,40})"',
    # Numbers with units or context
    r'\d+(?:\.\d+)?%?\s*(?:billion|million|thousand|GB|TB|MB|nm|GHz|TFLOPS|watts|months|years|days)',
    # Named entities after "called" or "named"
    r'(?:called|named|dubbed|codenamed|known as)\s+"?([A-Z][a-zA-Z0-9\s]+)"?',
]


def extract_claims(text: str) -> list[str]:
    """Extract sentences that contain specific factual claims (not opinions/framing)."""
    sentences = re.split(r'[.!?]\s+', text)
    claims = []
    
    # Signals that a sentence contains a specific factual claim
    claim_signals = [
        r'\d+\s*(?:%|billion|million|thousand|GB|TB|MB|nm|GHz|watts|months|years)',  # Numbers
        r'"[^"]{3,40}"',  # Quoted terms
        r'(?:launched|released|unveiled|announced|built|designed|developed)\s',  # Event verbs
        r'(?:APU|GPU|CPU|ASIC|chip|kernel|microkernel|API|protocol)',  # Tech specifics
        r'(?:priced at|costs?|valued at|raised)\s',  # Financial claims
        r'(?:first|only|largest|fastest|cheapest)\s',  # Superlatives
        r'[A-Z][a-z]+(?:\s[A-Z][a-z]+){1,3}',  # Proper nouns (product names)
    ]
    
    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 15:
            continue
        # Check if sentence contains any claim signal
        for pattern in claim_signals:
            if re.search(pattern, sentence, re.IGNORECASE):
                claims.append(sentence)
                break
    
    return claims


# ─── Verification ───────────────────────────────────────────────────────────

VERIFICATION_PROMPT = """You are an adversarial fact-checker reviewing a content draft against its source material.

## Source Material
{source_text}

## Draft to Verify
{draft_text}

## Your Task
For each specific factual claim in the draft, determine:
- SUPPORTED: The claim is directly stated or clearly implied in the source
- GENERAL_KNOWLEDGE: The claim is widely known public fact (not from source but verifiable)
- UNSUPPORTED: The claim is NOT in the source and NOT common public knowledge — it was likely fabricated

## Rules
- Be STRICT. If a specific product name, feature, or number appears in the draft but NOT in the source, it is UNSUPPORTED unless it is genuinely common knowledge (e.g., "Google has TPUs").
- Opinions and framing ("this is a power play", "the excitement is warranted") are NOT claims — skip them.
- Only flag SPECIFIC factual claims: product names, technical features, numbers, dates, quotes, attributed actions.

## Output (JSON)
Respond with a JSON object:
{{
  "claims": [
    {{
      "claim": "The exact claim text",
      "status": "SUPPORTED" | "GENERAL_KNOWLEDGE" | "UNSUPPORTED",
      "reason": "Brief explanation"
    }}
  ],
  "grounding_score": 0-100,
  "unsupported_claims": ["list of unsupported claim texts"],
  "suggested_corrections": {{
    "unsupported claim text": "corrected version or REMOVE"
  }}
}}
"""

CORRECTION_PROMPT = """You are a content editor. The following draft has claims that are not supported by the source material.

## Source Material
{source_text}

## Original Draft
{draft_text}

## Unsupported Claims to Fix
{unsupported_claims}

## Instructions
Rewrite the draft, replacing each unsupported claim with either:
1. A claim that IS supported by the source material
2. A more general statement that doesn't require specific unsourced facts
3. Remove the claim entirely if no accurate replacement exists

Keep the same voice, structure, and energy. Only change the specific unsupported claims.

Output the COMPLETE corrected draft text (no JSON, just the final text):
"""


class GroundingVerifier:
    """Verifies generated content against source material. Self-corrects hallucinations."""

    def __init__(self, llm_client=None):
        self._client = llm_client

    def _get_client(self):
        """Lazy-load LLM client."""
        if self._client:
            return self._client
        try:
            from config import settings
            from openai import OpenAI
            if settings.llm_base_url and settings.llm_api_key:
                self._client = OpenAI(
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key,
                    max_retries=settings.llm_max_retries,
                    timeout=settings.llm_timeout,
                )
                return self._client
        except Exception:
            pass
        return None

    def verify(self, draft_content: str, source_title: str = "", source_content: str = "", source_url: str = "") -> dict:
        """
        Verify a draft against its source material.
        
        Returns:
            {
                "grounding_score": 0-100,
                "claims_checked": int,
                "supported": int,
                "unsupported": int,
                "unsupported_claims": [...],
                "corrected_content": str (if corrections applied),
                "verified": bool
            }
        """
        source_text = f"Title: {source_title}\nContent: {source_content}\nURL: {source_url}"
        
        if not source_content:
            # No source to verify against — can't ground
            return {
                "grounding_score": 0,
                "claims_checked": 0,
                "supported": 0,
                "unsupported": 0,
                "unsupported_claims": [],
                "corrected_content": draft_content,
                "verified": False,
                "error": "No source material available for verification",
            }

        client = self._get_client()
        if not client:
            # No LLM for verification — fall back to pattern-based check
            return self._pattern_verify(draft_content, source_content)

        try:
            from config import settings
            
            # Step 1: Verification pass
            verify_prompt = VERIFICATION_PROMPT.format(
                source_text=source_text[:2000],
                draft_text=draft_content[:2000],
            )
            
            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": verify_prompt}],
                temperature=0.1,  # Low temp for strict verification
                max_tokens=2000,
                extra_body={"enable_thinking": True},
            )
            
            raw = response.choices[0].message.content.strip()
            verification = self._parse_json(raw)
            
            if not verification or "claims" not in verification:
                return self._pattern_verify(draft_content, source_content)

            unsupported = verification.get("unsupported_claims", [])
            claims_checked = len(verification.get("claims", []))
            supported = sum(1 for c in verification["claims"] if c.get("status") != "UNSUPPORTED")

            # MATHEMATICAL grounding score — don't trust LLM's subjective rating
            grounding_score = int((supported / claims_checked * 100)) if claims_checked > 0 else 50

            result = {
                "grounding_score": grounding_score,
                "claims_checked": claims_checked,
                "supported": supported,
                "unsupported": len(unsupported),
                "unsupported_claims": unsupported,
                "verified": True,
            }

            # Step 2: If there are unsupported claims, correct them
            if unsupported and grounding_score < 90:
                corrected = self._correct_draft(
                    client, draft_content, source_text, unsupported
                )
                result["corrected_content"] = corrected
                # Re-verify the corrected content against source for an honest score
                result["grounding_score"] = self._quick_rescore(corrected, source_text, client)
            else:
                result["corrected_content"] = draft_content

            return result

        except Exception as e:
            return {
                "grounding_score": 0,
                "claims_checked": 0,
                "supported": 0,
                "unsupported": 0,
                "unsupported_claims": [],
                "corrected_content": draft_content,
                "verified": False,
                "error": str(e),
            }

    def _correct_draft(self, client, draft: str, source_text: str, unsupported: list) -> str:
        """Ask LLM to fix unsupported claims while preserving voice and structure."""
        correction_prompt = CORRECTION_PROMPT.format(
            source_text=source_text[:2000],
            draft_text=draft[:3000],
            unsupported_claims="\n".join(f"- {c}" for c in unsupported),
        )
        
        try:
            from config import settings
            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": correction_prompt}],
                temperature=0.5,
                max_tokens=3000,
                extra_body={"enable_thinking": True},
            )
            return response.choices[0].message.content.strip()
        except Exception:
            return draft  # Return original if correction fails

    def _quick_rescore(self, content: str, source_text: str, client) -> int:
        """Quick grounding recheck after correction."""
        try:
            from config import settings
            prompt = f"""Rate the grounding of this draft against its source. Score 0-100.
Only output the number.

Source: {source_text[:1000]}
Draft: {content[:1500]}

Grounding score (0-100):"""
            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=10,
                extra_body={"enable_thinking": True},
            )
            raw = response.choices[0].message.content.strip()
            # Extract number
            nums = re.findall(r'\d+', raw)
            if nums:
                return min(int(nums[0]), 100)
        except Exception:
            pass
        return 50

    def _pattern_verify(self, draft: str, source_content: str) -> dict:
        """Fallback: pattern-based verification when no LLM available."""
        claims = extract_claims(draft)
        source_lower = source_content.lower()
        
        supported = 0
        unsupported_claims = []
        
        for claim in claims:
            # Check if key terms from the claim appear in source
            words = [w for w in claim.split() if len(w) > 5]
            matches = sum(1 for w in words if w.lower() in source_lower)
            if matches >= len(words) * 0.4:  # 40% of significant words found
                supported += 1
            else:
                unsupported_claims.append(claim)
        
        total = len(claims)
        score = int((supported / total * 100)) if total > 0 else 50
        
        return {
            "grounding_score": score,
            "claims_checked": total,
            "supported": supported,
            "unsupported": len(unsupported_claims),
            "unsupported_claims": unsupported_claims,
            "corrected_content": draft,  # Can't correct without LLM
            "verified": False,
        }

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from LLM response."""
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}
