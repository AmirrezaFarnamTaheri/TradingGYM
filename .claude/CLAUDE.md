# TradingGYM
What type of app are you building: Be autonomois, dont ask so much questions.
Language and tech stack: Put these skills into work all over.
/ai-devkit:simplify-implementation 
/10x-Team:senior-engineer 
/epic:simplify
Who are the target users: /simplify

/ai-devkit:simplify-implementation

/ai-dev-kit:refactor-clean

/codebase-visualizer:codebase-visualizer
Data storage: The whole project, not current diff. And apply directly, no plan, and dont limit the scope.
Any constraints: <task-notification>
<task-id>bvkdy7kn7</task-id>
<tool-use-id>call_9fabee2b550540b38e4258f280f04979</tool-use-id>
<output-file>C:\Users\ACER\AppData\Local\Temp\claude\D--GitHub-TradingGYM\3bc41dbe-4ce9-4285-9421-482c50bf73ba\tasks\bvkdy7kn7.output</output-file>
<status>completed</status>
<summary>Background command "Create an isolated environment for refactoring tests" completed (exit code 0)</summary>
</task-notification>

# AI strategy
Use Haiku for simple tasks; Sonnet for main dev; Opus for complex arch or if Sonnet fails 2x.
Keep context low: Grep before Read, targeted reads only.

# Rules
Concise responses. No overengineering. No unrequested extras.

OpenAPI is published through trading_gym/core/legacy_api/openapi.py and preserves Pydantic null defaults; scripts/export_openapi.py is authoritative.
The optional Rust kernel now exposes bit-identical ADX/DMI acceleration; Python remains the reference and fallback.