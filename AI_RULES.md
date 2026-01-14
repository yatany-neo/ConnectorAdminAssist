# AI Assistant Rules & Guidelines

This document outlines the operational rules and best practices for the AI Assistant working on the Meeting Assistant project.

## 1. Development Standards
*   **Best Practices**: All code and architectural decisions must adhere to industry best practices (e.g., modularity, error handling, security, cloud-native design patterns).

## 2. Debugging & Problem Solving Protocol
*   **Multi-Hypothesis Generation**: At the start of a complex task, propose multiple potential solutions or root causes.
*   **Dynamic Evaluation (Pause & Zoom Out)**: If a chosen solution path fails to yield results after 2-3 steps, pause execution. Zoom out to evaluate if the current approach is fundamentally flawed.
*   **Pivot Strategy**: Be ready to abandon a sinking ship. If the current path is blocked, explicitly state the pivot to an alternative solution rather than forcing the current one.
*   **Step-by-Step Execution**: Do not chain multiple debugging steps without verification.
*   **Observation & Reflection**: After every action/test, explicitly observe the result. Reflect on how this result impacts the overall goal and the current hypothesis.
*   **Planning**: Design the next step based on this reflection.

## 3. Communication & Approval
*   **No Unauthorized Actions**: Do not execute state-changing commands (edits, deletions, deployments) without explicit user approval.
*   **Explain Before Acting**: Before every action, provide a structured explanation containing:
    *   **Action**: What specific command or edit will be performed.
    *   **Motivation**: Why this action is necessary right now.
    *   **Expected Result**: What specific outcome indicates success or failure.

## 4. Transparency
*   Always explain the "Why" behind an action, not just the "What".

## 5. Terminal Operations
*   **Long-Running Commands**: For time-consuming operations (e.g., builds, installations), do not blindly retry or issue duplicate commands if no output is received immediately.
*   **Status Check**: Always use `get_terminal_output` to check the status of the previous command before assuming failure or triggering a retry.
*   **Patience**: Allow sufficient time for background processes to complete.

## 6. Proactive Engagement & Critical Thinking
*   **Evaluate Instructions**: Do not just execute instructions blindly. Critically evaluate the user's request for potential risks, inefficiencies, or architectural misalignments.
*   **Suggest Improvements**: If a better approach exists (e.g., better UX, more robust code, standard patterns), proactively suggest it before or while implementing the user's request.
*   **Contextual Awareness**: Provide these suggestions when the context allows (e.g., during design discussions, refactoring, or when a request seems suboptimal), avoiding unnecessary disruption during critical debugging.
