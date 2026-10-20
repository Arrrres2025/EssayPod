"""Evidence-grounded research swarm for EssayPod's compiled LLM Wiki."""

from .swarm import SwarmRetrievalService

AgenticRetrievalService = SwarmRetrievalService

__all__ = ["AgenticRetrievalService", "SwarmRetrievalService"]
