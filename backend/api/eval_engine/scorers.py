import re
import os
import json
import logging
from typing import Dict, Any, Optional
from openai import OpenAI

logger = logging.getLogger("promptops.eval_engine")

class ScorerRegistry:
    """Registry to manage and trigger different evaluation scorers."""
    
    @staticmethod
    def exact_match(output: str, expected: str, **kwargs) -> Dict[str, Any]:
        """Strict or normalized exact match scoring."""
        out_clean = str(output).strip().lower()
        exp_clean = str(expected).strip().lower()
        score = 1.0 if out_clean == exp_clean else 0.0
        return {
            "score": score,
            "details": f"Exact match: '{out_clean}' == '{exp_clean}'"
        }

    @staticmethod
    def regex_match(output: str, pattern: str, **kwargs) -> Dict[str, Any]:
        """Verifies if the output matches a specific regular expression pattern."""
        if not pattern:
            return {"score": 1.0, "details": "No regex pattern provided."}
        try:
            match = re.search(pattern, str(output), re.IGNORECASE | re.DOTALL)
            score = 1.0 if match else 0.0
            return {
                "score": score,
                "details": f"Regex match for pattern '{pattern}': {'FOUND' if match else 'NOT FOUND'}"
            }
        except re.error as e:
            return {
                "score": 0.0,
                "details": f"Invalid regex pattern '{pattern}': {e}"
            }

    @staticmethod
    def rouge_l(output: str, expected: str, **kwargs) -> Dict[str, Any]:
        """
        Longest Common Subsequence (LCS) based ROUGE-L score estimation.
        Self-contained implementation avoiding heavy external dependency overhead.
        """
        out_tokens = str(output).lower().split()
        exp_tokens = str(expected).lower().split()
        
        if not out_tokens or not exp_tokens:
            return {"score": 0.0, "details": "Empty output or expected tokens."}

        # Dynamic programming table for LCS
        n, m = len(out_tokens), len(exp_tokens)
        dp = [[0] * (m + 1) for _ in range(n + 1)]
        
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                if out_tokens[i - 1] == exp_tokens[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
                    
        lcs_len = dp[n][m]
        
        # Calculate precision, recall, and F1-score
        recall = lcs_len / len(exp_tokens)
        precision = lcs_len / len(out_tokens)
        
        if (recall + precision) == 0:
            f1 = 0.0
        else:
            f1 = (2 * precision * recall) / (precision + recall)
            
        return {
            "score": round(f1, 4),
            "details": f"LCS word length: {lcs_len}. Recall: {recall:.2f}, Precision: {precision:.2f}"
        }

    @staticmethod
    def llm_judge(
        output: str, 
        expected: str, 
        inputs: Dict[str, Any], 
        criteria: str = "faithfulness", 
        **kwargs
    ) -> Dict[str, Any]:
        """
        Model-based evaluator (LLM-as-Judge) using an OpenAI-compatible API client.
        Supported criteria: 'faithfulness', 'relevance'.
        """
        api_key = os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        model = os.environ.get("PROMPTOPS_JUDGE_MODEL", "gpt-4o-mini")

        if not api_key:
            # Safe mock fallback if no API key is set so evaluations don't fail hard
            logger.warning("OPENAI_API_KEY environment variable not found. Using scoring fallback.")
            # Simple fallback check
            mock_score = 1.0 if str(expected).lower()[:30] in str(output).lower() else 0.5
            return {
                "score": mock_score,
                "reasoning": "API key missing. Executed fallback string similarity check."
            }

        client = OpenAI(api_key=api_key, base_url=base_url)

        prompts = {
            "faithfulness": (
                "You are an expert AI judge. Rate the FAITHFULNESS of the output relative to the expected reference output. "
                "Look for any contradictions, hallucinations, or fabrication of facts. "
                f"\n\nContext/Inputs: {json.dumps(inputs)}"
                f"\nReference Output (Ground Truth): {expected}"
                f"\nCandidate Output to Evaluate: {output}"
                "\n\nRespond ONLY with a JSON object in this format: "
                '{"score": <float between 0.0 and 1.0>, "reasoning": "<brief explanation of score>"}'
            ),
            "relevance": (
                "You are an expert AI judge. Rate the RELEVANCE and format compliance of the candidate output. "
                "Determine if it directly answers the user's intent and satisfies formatting constraints. "
                f"\n\nContext/Inputs: {json.dumps(inputs)}"
                f"\nCandidate Output to Evaluate: {output}"
                "\n\nRespond ONLY with a JSON object in this format: "
                '{"score": <float between 0.0 and 1.0>, "reasoning": "<brief explanation of score>"}'
            )
        }

        system_prompt = prompts.get(criteria, prompts["faithfulness"])

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a helpful grading assistant designed to output JSON."},
                    {"role": "user", "content": system_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=250
            )
            
            raw_content = response.choices[0].message.content
            result = json.loads(raw_content)
            
            score = float(result.get("score", 0.0))
            reasoning = result.get("reasoning", "No explanation provided.")
            
            return {
                "score": max(0.0, min(1.0, score)),
                "reasoning": reasoning
            }
        except Exception as e:
            logger.error(f"Error calling LLM judge API: {e}")
            return {
                "score": 0.0,
                "reasoning": f"Failed to execute LLM grading: {e}"
            }
