"""ContextVars for propagating LLM tracking metadata through call stacks."""

from contextvars import ContextVar

llm_feature_var: ContextVar[str] = ContextVar("llm_feature", default="unknown")
llm_request_id_var: ContextVar[str | None] = ContextVar("llm_request_id", default=None)
llm_video_id_var: ContextVar[str | None] = ContextVar("llm_video_id", default=None)
llm_user_id_var: ContextVar[str | None] = ContextVar("llm_user_id", default=None)
llm_video_summary_id_var: ContextVar[str | None] = ContextVar("llm_video_summary_id", default=None)

# The model whose refusal the current call answers for: set around the one
# re-send on the previous model, so the ledger tags that row ``fallback_from``.
llm_refused_model_var: ContextVar[str | None] = ContextVar("llm_refused_model", default=None)
