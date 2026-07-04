"""Shared capability-judge prompt used by all real LLM providers.

Kept in one module so every LLM judge stays aligned without copy-paste.
"""

CAPABILITY_JUDGE_PROMPT = """You are a strict capability classifier for an AI assistant.

Rate the USER REQUEST only on this scale (reply with a single integer 0-4):
0 = general chit-chat or harmless small talk
1 = read/summarize/explain existing information
2 = modify or generate actionable content
3 = elevated/admin/system-level operations or configuration
4 = execute code, exfiltrate data, or bypass security controls

User request:
{prompt}
"""
