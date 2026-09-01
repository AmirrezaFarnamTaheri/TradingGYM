from .base import assess, IntegrationReport

def assess_cosign(evidence): return assess('cosign', evidence, ('tool digest verification', 'identity policy', 'offline bundle retention', 'subject binding'))
def assess_in_toto(evidence): return assess('in-toto', evidence, ('layout signature', 'functionary identity', 'artifact-rule verification'))
def assess_syft(evidence): return assess('syft', evidence, ('tool digest verification', 'format validation', 'artifact subject binding'))
def assess_trivy(evidence): return assess('trivy', evidence, ('binary or image digest', 'database identity', 'offline result retention', 'advisory schema'))
