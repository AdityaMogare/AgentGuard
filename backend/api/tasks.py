from celery import shared_task
import logging

from api.eval_engine.runner import run_eval_suite

logger = logging.getLogger("promptops.tasks")

@shared_task(name="api.tasks.execute_evaluation")
def execute_evaluation(eval_run_id: str):
    """
    Asynchronous Celery task that triggers the evaluation suite
    for a given EvalRun ID.
    """
    logger.info(f"Starting background evaluation task for EvalRun {eval_run_id}")
    run_eval_suite(eval_run_id)
    logger.info(f"Background evaluation task for EvalRun {eval_run_id} finished")
