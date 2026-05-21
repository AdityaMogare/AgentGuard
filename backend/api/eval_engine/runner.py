import time
import json
import os
import random
import logging
from django.utils import timezone
from openai import OpenAI

from api.models import EvalRun, EvalResult, Trace, PromptVersion
from .scorers import ScorerRegistry
from sdk.promptops.utils import count_tokens, calculate_cost

logger = logging.getLogger("promptops.eval_engine")

def run_eval_suite(eval_run_id: str):
    """
    Executes an evaluation run. Transitions status, loops through dataset pairs,
    queries the LLM (or simulates outputs if offline), scores responses,
    logs Observability Traces, and writes results to the database.
    """
    try:
        eval_run = EvalRun.objects.get(id=eval_run_id)
    except EvalRun.DoesNotExist:
        logger.error(f"EvalRun {eval_run_id} not found.")
        return

    eval_run.status = "RUNNING"
    eval_run.save()

    try:
        prompt_version = eval_run.prompt_version
        dataset = eval_run.dataset
        template = prompt_version.template
        model = prompt_version.model
        params = prompt_version.parameters
        
        # Load API configs
        api_key = os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
        
        client = None
        if api_key:
            client = OpenAI(api_key=api_key, base_url=base_url)

        total_score = 0.0
        passed_cases = 0
        total_cases = len(dataset.data_pairs)
        
        if total_cases == 0:
            eval_run.status = "COMPLETED"
            eval_run.mean_score = 1.0
            eval_run.pass_rate = 1.0
            eval_run.save()
            return

        results_to_create = []

        for index, test_case in enumerate(dataset.data_pairs):
            # Each pair looks like: {"inputs": {"var1": "val1"}, "expected_output": "..."}
            inputs = test_case.get("inputs", {})
            expected = test_case.get("expected_output", "")

            # 1. Format the template prompt with input variables
            try:
                formatted_prompt = template.format(**inputs)
            except Exception as e:
                formatted_prompt = f"{template}\nInputs: {inputs}"

            # 2. Get LLM response (or execute simulated model-run)
            start_time = time.perf_counter()
            output = ""
            status = "SUCCESS"
            error_message = None

            if client:
                # Real LLM call
                try:
                    chat_completion = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": formatted_prompt}],
                        temperature=params.get("temperature", 0.7),
                        max_tokens=params.get("max_tokens", 500)
                    )
                    output = chat_completion.choices[0].message.content
                except Exception as e:
                    status = "FAILED"
                    error_message = str(e)
                    output = ""
            else:
                # Intelligent offline simulation mode
                time.sleep(0.05) # Simulate slight network latency
                
                # Check if the prompt has a regression injected
                # e.g., if the user modified the system prompt in a way that breaks instructions
                is_regressed = "regression" in template.lower() or "hallucinate" in template.lower()
                
                if is_regressed:
                    # Simulate high variance or factual error (regression)
                    if random.random() < 0.35:
                        output = f"Simulated Hallucination: {expected[:15]}... This response deviates from expected instructions."
                    else:
                        output = expected
                else:
                    # High quality response
                    output = expected

            end_time = time.perf_counter()
            latency_ms = (end_time - start_time) * 1000.0

            # 3. Token count & Cost calculation
            prompt_tokens = count_tokens(formatted_prompt, model)
            completion_tokens = count_tokens(output, model)
            cost = calculate_cost(prompt_tokens, completion_tokens, model)

            # 4. Save the run trace for observability
            trace = Trace.objects.create(
                prompt_version=prompt_version,
                name=f"eval_case_{index}",
                input_variables=inputs,
                output=output,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost=cost,
                status=status,
                error_message=error_message
            )

            # 5. Execute Evaluation Scorers
            exact_res = ScorerRegistry.exact_match(output, expected)
            rouge_res = ScorerRegistry.rouge_l(output, expected)
            
            # Run LLM judge if API key is active, or use offline similarity proxy
            judge_res = ScorerRegistry.llm_judge(output, expected, inputs)
            
            # Combine scores
            score_details = {
                "exact_match": exact_res["score"],
                "rouge_l": rouge_res["score"],
                "llm_judge": judge_res["score"]
            }

            # Overall case score is the average of scorers
            case_score = (score_details["exact_match"] + score_details["rouge_l"] + score_details["llm_judge"]) / 3.0
            is_pass = case_score >= 0.75 # Threshold to define "pass"

            if is_pass:
                passed_cases += 1
            total_score += case_score

            # Save single result
            EvalResult.objects.create(
                eval_run=eval_run,
                trace=trace,
                inputs=inputs,
                output=output,
                expected_output=expected,
                score_details=score_details,
                is_pass=is_pass
            )

        # 6. Aggregate run report details
        eval_run.status = "COMPLETED"
        eval_run.mean_score = round(total_score / total_cases, 4)
        eval_run.pass_rate = round(passed_cases / total_cases, 4)
        eval_run.save()

        logger.info(f"EvalRun {eval_run_id} completed successfully. Mean Score: {eval_run.mean_score}, Pass Rate: {eval_run.pass_rate}")

    except Exception as e:
        logger.error(f"EvalRun {eval_run_id} failed: {e}", exc_info=True)
        eval_run.status = "FAILED"
        eval_run.save()
