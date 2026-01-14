# Connector Configuration Field Strategy

This document outlines the strategic logic used by the Admin Assistant to generate suggestions for Graph Connector configuration fields. These strategies are derived from the "Chain Analysis" of Copilot's internal *Skill Discovery Service* mechanics, balancing **AI Discoverability (SEO)** with **Administrative Management**.

## 1. Display Name

**Impact**: 
- Indexed for user search.
- Used by Copilot to identify the data source in the UI.
- High impact on keywords.

**Strategy**: `[Tool Name] [Business Object] [YYYYMMDD]`

**Rationale**:
- **Tool Name + Business Object** (e.g., "Jira Tickets"): Maximizes "High-Weight Keyword" hits in the SemanticFBV algorithm. Ensures the semantic distance is low (< 0.6).
- **Date Suffix** (e.g., "20260113"): Satisfies the Admin's need for version control, chronological sorting, and collision avoidance without significantly diluting the semantic score.

**Example**:
- Suggestion: `Jira Tickets 20260113`
- Bad: `MyJiraConn` (No meaning), `Jira` (Too generic), `Test_Connection_1` (No business value).

---

## 2. Description

**Impact**: 
- **Highest Impact**. The primary source for the semantic distance calculation.
- If the description is generic, the tool is filtered out before it even reaches the LLM context window.

**Strategy**: "Keyword Stuffing" / Rich Semantic Description

**Rationale**:
- Must explicitly state **WHAT** the data is (Synonyms: bugs, stories, tasks) and **HOW** it is used.
- The goal is to aggressively minimize semantic distance to potential user prompts.

**Template**: 
> "Contains all active [Tool] [Business Objects], [Synonyms], and [Related Entities] for [Purpose/Action]."

**Example**:
- Suggestion: "Contains all active Jira software tickets, bugs, and user stories for tracking development progress and project status."
- Bad: "Test connector", "Jira Data", "Connection for Project X".

---

## 3. User Interaction Logic (Backend)

The backend provides structured responses to ensure Admins can distinguish between *strategic advice* and *suggested values*.

**Output Structure**:
- **Insight**: Explains the rationale (Search Strategy / Admin Versioning).
- **Suggestion**: A copy-pasteable value provided in a code block for clear visibility.
  - Format: `***Insight**: ... **Suggestion**: \`\`\`value\`\`\`*`
