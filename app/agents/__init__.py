"""LLM agents. Emit validated proposals only -- never write to the database,
never hold broker/session credentials, never call anything with authority.
See app.agents.base for the ResearchAgent contract.
"""
