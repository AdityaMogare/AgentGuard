from setuptools import setup, find_packages

setup(
    name="promptops",
    version="0.1.0",
    description="PromptOps Python SDK - LLM Pipeline Tracing & Evaluation CI/CD Client",
    author="PromptOps Team",
    packages=find_packages(),
    install_requires=[
        "requests>=2.25.0",
        "tiktoken>=0.3.0",
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
)
