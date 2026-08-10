"""
Chat / customer-service agent service.

Conversational front door + LangGraph orchestrator for BMMB SME financing
(brief §5, §6): classifies intent, answers in-scope questions, runs an
indicative eligibility pre-check, and hands off to humans/downstream agents.

See services/chat/app/main.py for the FastAPI wiring -- it exports both a
standalone `app` (for `uvicorn app.main:app`) and the pieces a host app needs
to mount this in-process (`router`, `lifespan`, `get_orchestrator`), mirroring
the router-export convention used by services.{aggregation,bbox_generator,
mcp,validation}.
"""
