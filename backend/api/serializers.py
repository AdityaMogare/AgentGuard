from rest_framework import serializers
from .models import PromptVersion, Trace, Dataset, EvalRun, EvalResult

class PromptVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromptVersion
        fields = '__all__'


class TraceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Trace
        fields = '__all__'


class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = '__all__'


class EvalResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvalResult
        fields = '__all__'


class EvalRunSerializer(serializers.ModelSerializer):
    prompt_version_details = PromptVersionSerializer(source='prompt_version', read_only=True)
    dataset_name = serializers.CharField(source='dataset.name', read_only=True)
    
    class Meta:
        model = EvalRun
        fields = [
            'id', 
            'prompt_version', 
            'prompt_version_details',
            'dataset', 
            'dataset_name', 
            'status', 
            'mean_score', 
            'pass_rate', 
            'created_at'
        ]
