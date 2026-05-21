import uuid
from django.db import models

class PromptVersion(models.Model):
    """
    Represents a specific version of a prompt, uniquely identified by the
    SHA-256 hash of its template, model name, and parameters.
    """
    id = models.CharField(max_length=64, primary_key=True) # SHA-256 hash
    template = models.TextField()
    model = models.CharField(max_length=100)
    parameters = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["model"]),
        ]

    def __str__(self):
        return f"{self.model} - {self.id[:8]}"


class Trace(models.Model):
    """
    Telemetry captured by the promptops SDK for an individual LLM call.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    prompt_version = models.ForeignKey(
        PromptVersion, 
        on_delete=models.CASCADE, 
        related_name="traces"
    )
    name = models.CharField(max_length=200, blank=True, db_index=True)
    input_variables = models.JSONField(default=dict)
    output = models.TextField(blank=True)
    latency_ms = models.FloatField()
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    cost = models.FloatField(default=0.0)
    status = models.CharField(max_length=50, default="SUCCESS")
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["prompt_version", "created_at"]),
        ]

    def __str__(self):
        return f"Trace {self.id} ({self.name})"


class Dataset(models.Model):
    """
    A collection of input/expected-output pairs used for evaluations.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=250, unique=True)
    # data_pairs is expected to be a list of dicts: [{"inputs": {...}, "expected_output": "..."}]
    data_pairs = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class EvalRun(models.Model):
    """
    Represents an evaluation run of a PromptVersion against a Dataset.
    """
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("RUNNING", "Running"),
        ("COMPLETED", "Completed"),
        ("FAILED", "Failed"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    prompt_version = models.ForeignKey(
        PromptVersion, 
        on_delete=models.CASCADE, 
        related_name="eval_runs"
    )
    dataset = models.ForeignKey(
        Dataset, 
        on_delete=models.CASCADE, 
        related_name="eval_runs"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    mean_score = models.FloatField(null=True, blank=True)
    pass_rate = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"EvalRun {self.id[:8]} ({self.status})"


class EvalResult(models.Model):
    """
    Stores the result of evaluating a single test case within an EvalRun.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_run = models.ForeignKey(
        EvalRun, 
        on_delete=models.CASCADE, 
        related_name="results"
    )
    # Reference to the actual trace generated during the eval run execution (optional)
    trace = models.ForeignKey(
        Trace, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="eval_results"
    )
    inputs = models.JSONField(default=dict)
    output = models.TextField(blank=True)
    expected_output = models.TextField(blank=True)
    # score_details example: {"exact_match": 1.0, "rouge_l": 0.85, "faithfulness": 0.90}
    score_details = models.JSONField(default=dict)
    is_pass = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Result {self.id[:8]} - Pass: {self.is_pass}"
