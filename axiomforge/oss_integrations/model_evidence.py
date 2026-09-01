from .base import assess, IntegrationReport

def assess_mlflow(evidence): return assess('mlflow', evidence, ('tenant tags', 'dataset and code digests', 'artifact immutability', 'restore drill'))
