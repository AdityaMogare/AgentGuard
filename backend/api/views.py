import json
import logging
from django.db.models import Avg, Count, Sum, Q
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.decorators import action

from .models import PromptVersion, Trace, Dataset, EvalRun, EvalResult
from .serializers import (
    PromptVersionSerializer, 
    TraceSerializer, 
    DatasetSerializer, 
    EvalRunSerializer, 
    EvalResultSerializer
)
from .tasks import execute_evaluation

logger = logging.getLogger("promptops.views")

class TraceIngestView(APIView):
    """
    Endpoint for the promptops Python SDK to log traces asynchronously.
    Creates the prompt version if it doesn't already exist.
    """
    def post(self, request, *args, **kwargs):
        payload = request.data
        if not payload or "prompt_version" not in payload or "trace_data" not in payload:
            return Response(
                {"error": "Malformed trace payload. Must contain 'prompt_version' and 'trace_data'."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        version_data = payload["prompt_version"]
        trace_data = payload["trace_data"]
        
        # 1. Get or create prompt version
        version_hash = version_data.get("hash")
        if not version_hash:
            return Response(
                {"error": "Missing prompt version hash."},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        prompt_version, created = PromptVersion.objects.get_or_create(
            id=version_hash,
            defaults={
                "template": version_data.get("template", ""),
                "model": version_data.get("model", "default"),
                "parameters": version_data.get("parameters", {})
            }
        )
        
        # 2. Save trace linked to prompt version
        trace = Trace.objects.create(
            prompt_version=prompt_version,
            name=trace_data.get("name", "unnamed_call"),
            input_variables=trace_data.get("input_variables", {}),
            output=trace_data.get("output", ""),
            latency_ms=float(trace_data.get("latency_ms", 0.0)),
            prompt_tokens=int(trace_data.get("prompt_tokens", 0)),
            completion_tokens=int(trace_data.get("completion_tokens", 0)),
            cost=float(trace_data.get("cost", 0.0)),
            status=trace_data.get("status", "SUCCESS"),
            error_message=trace_data.get("error_message")
        )
        
        return Response(
            {"message": "Trace logged successfully", "trace_id": str(trace.id)},
            status=status.HTTP_201_CREATED
        )


class PromptVersionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Endpoints to list prompt versions, view details, and retrieve
    observability analytics.
    """
    queryset = PromptVersion.objects.all()
    serializer_class = PromptVersionSerializer

    def list(self, request, *args, **kwargs):
        """
        List all prompt versions with integrated aggregate statistics:
        total traces, mean latency, total cost, and latest eval pass rate.
        """
        versions = self.get_queryset()
        data = []
        
        for version in versions:
            stats = Trace.objects.filter(prompt_version=version).aggregate(
                total_traces=Count("id"),
                avg_latency=Avg("latency_ms"),
                total_cost=Sum("cost")
            )
            
            # Find the latest completed evaluation run for this version
            latest_eval = EvalRun.objects.filter(
                prompt_version=version, 
                status="COMPLETED"
            ).order_by("-created_at").first()
            
            serialized = self.get_serializer(version).data
            serialized["analytics"] = {
                "total_traces": stats["total_traces"] or 0,
                "avg_latency_ms": round(stats["avg_latency"] or 0.0, 2),
                "total_cost_usd": round(stats["total_cost"] or 0.0, 6),
                "latest_eval_score": latest_eval.mean_score if latest_eval else None,
                "latest_eval_pass_rate": latest_eval.pass_rate if latest_eval else None,
                "latest_eval_id": str(latest_eval.id) if latest_eval else None,
            }
            data.append(serialized)
            
        return Response(data)

    def retrieve(self, request, pk=None, *args, **kwargs):
        """Retrieve detail payload with historical latency timeline and run logs."""
        version = get_object_or_404(PromptVersion, id=pk)
        serialized = self.get_serializer(version).data
        
        # Aggregate stats
        traces_qs = Trace.objects.filter(prompt_version=version)
        stats = traces_qs.aggregate(
            total_traces=Count("id"),
            avg_latency=Avg("latency_ms"),
            total_cost=Sum("cost"),
            prompt_tokens=Sum("prompt_tokens"),
            completion_tokens=Sum("completion_tokens")
        )
        
        # Load historical traces timeline (limited to last 50 entries)
        recent_traces = TraceSerializer(traces_qs[:50], many=True).data
        
        # Load eval runs history
        eval_runs = EvalRunSerializer(
            EvalRun.objects.filter(prompt_version=version), 
            many=True
        ).data

        serialized["analytics"] = {
            "total_traces": stats["total_traces"] or 0,
            "avg_latency_ms": round(stats["avg_latency"] or 0.0, 2),
            "total_cost_usd": round(stats["total_cost"] or 0.0, 6),
            "total_prompt_tokens": stats["prompt_tokens"] or 0,
            "total_completion_tokens": stats["completion_tokens"] or 0,
        }
        serialized["recent_traces"] = recent_traces
        serialized["eval_runs"] = eval_runs
        
        return Response(serialized)


class DatasetViewSet(viewsets.ModelViewSet):
    """CRUD viewset to manage datasets of input/expected pairs."""
    queryset = Dataset.objects.all()
    serializer_class = DatasetSerializer


class EvalRunViewSet(viewsets.ModelViewSet):
    """
    Endpoints to trigger evaluations and examine execution status
    and scored test case outputs.
    """
    queryset = EvalRun.objects.all()
    serializer_class = EvalRunSerializer

    def create(self, request, *args, **kwargs):
        """
        Triggers an evaluation run. Starts async background worker tasks
        leveraging Celery.
        """
        prompt_version_id = request.data.get("prompt_version")
        dataset_id = request.data.get("dataset")

        if not prompt_version_id or not dataset_id:
            return Response(
                {"error": "Both 'prompt_version' (hash) and 'dataset' (uuid) are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        prompt_version = get_object_or_404(PromptVersion, id=prompt_version_id)
        dataset = get_object_or_404(Dataset, id=dataset_id)

        # Create the run in PENDING status
        eval_run = EvalRun.objects.create(
            prompt_version=prompt_version,
            dataset=dataset,
            status="PENDING"
        )

        # Trigger Celery worker (runs synchronously in eager/dev mode)
        execute_evaluation.delay(str(eval_run.id))

        serializer = self.get_serializer(eval_run)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None, *args, **kwargs):
        """Retrieve detailed run report and its associated scored test results."""
        eval_run = get_object_or_404(EvalRun, id=pk)
        serialized = self.get_serializer(eval_run).data
        
        results_qs = EvalResult.objects.filter(eval_run=eval_run)
        results_serialized = EvalResultSerializer(results_qs, many=True).data
        
        serialized["results"] = results_serialized
        return Response(serialized)


class CompareRunsView(APIView):
    """
    Side-by-side evaluation run comparison and regression detector.
    Averages metrics and aligns individual test cases.
    """
    def get(self, request, *args, **kwargs):
        run_a_id = request.query_params.get("run_a")
        run_b_id = request.query_params.get("run_b")

        if not run_a_id or not run_b_id:
            return Response(
                {"error": "Query parameters 'run_a' (baseline) and 'run_b' (candidate) are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        run_a = get_object_or_404(EvalRun, id=run_a_id)
        run_b = get_object_or_404(EvalRun, id=run_b_id)

        # Fetch results
        results_a = EvalResult.objects.filter(eval_run=run_a)
        results_b = EvalResult.objects.filter(eval_run=run_b)

        # Compute aggregate deltas
        score_a = run_a.mean_score or 0.0
        score_b = run_b.mean_score or 0.0
        pass_a = run_a.pass_rate or 0.0
        pass_b = run_b.pass_rate or 0.0
        
        delta_score = score_b - score_a
        delta_pass_rate = pass_b - pass_a

        # Align individual test cases by sorting/matching input variables
        # Using serialized inputs keys as unique matching signatures
        def get_input_signature(inputs_dict):
            return json.dumps(inputs_dict, sort_keys=True)

        dict_a = {get_input_signature(r.inputs): r for r in results_a}
        dict_b = {get_input_signature(r.inputs): r for r in results_b}

        comparisons = []
        regressed_count = 0
        improved_count = 0
        unchanged_count = 0

        # We loop through all keys present in B (the new version)
        for sig, res_b in dict_b.items():
            res_a = dict_a.get(sig)
            
            if not res_a:
                # Case only present in B, skip or mark as new
                continue
                
            # Average score calculation helper
            def avg_result_score(res):
                details = res.score_details or {}
                vals = list(details.values())
                return sum(vals) / len(vals) if vals else 0.0

            score_val_a = avg_result_score(res_a)
            score_val_b = avg_result_score(res_b)
            case_delta = score_val_b - score_val_a

            # Define classification
            # Regression is a significant score drop (e.g. > 0.05 score difference)
            if case_delta < -0.05:
                case_status = "REGRESSED"
                regressed_count += 1
            elif case_delta > 0.05:
                case_status = "IMPROVED"
                improved_count += 1
            else:
                case_status = "UNCHANGED"
                unchanged_count += 1

            comparisons.append({
                "status": case_status,
                "inputs": res_b.inputs,
                "expected": res_b.expected_output,
                "output_a": res_a.output,
                "output_b": res_b.output,
                "score_a": round(score_val_a, 4),
                "score_b": round(score_val_b, 4),
                "delta": round(case_delta, 4),
                "score_details_a": res_a.score_details,
                "score_details_b": res_b.score_details
            })

        response_payload = {
            "baseline_run": {
                "id": str(run_a.id),
                "prompt_version_id": run_a.prompt_version.id,
                "mean_score": score_a,
                "pass_rate": pass_a,
                "created_at": run_a.created_at
            },
            "candidate_run": {
                "id": str(run_b.id),
                "prompt_version_id": run_b.prompt_version.id,
                "mean_score": score_b,
                "pass_rate": pass_b,
                "created_at": run_b.created_at
            },
            "summary": {
                "score_delta": round(delta_score, 4),
                "pass_rate_delta": round(delta_pass_rate, 4),
                "total_comparisons": len(comparisons),
                "regressed_cases": regressed_count,
                "improved_cases": improved_count,
                "unchanged_cases": unchanged_count
            },
            "comparisons": comparisons
        }

        return Response(response_payload)
