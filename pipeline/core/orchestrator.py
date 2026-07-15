"""Main orchestrator - coordinates all pipeline components."""

import logging
import time
from datetime import datetime
from typing import Optional, Iterator

logger = logging.getLogger(__name__)

from schemas.intent import ParsedIntent, IntentType
from schemas.response import PipelineResponse, AuditLog

from .intent_parser import IntentParser
from .planner import QueryPlanner
from .executor import ToolExecutor
from .explainer import ResponseExplainer
from .audit import AuditLogger
from ..insights.transaction_flow import get_transaction_insights_if_needed
from utils.helpers import mask_customer_id
from config.settings import PARSER_MODEL, EXPLAINER_MODEL, STREAM_DELAY


INSIGHT_INTENTS = {
    IntentType.LENDER_PROFILE,
    IntentType.CUSTOMER_REPORT,
    IntentType.FINANCIAL_OVERVIEW,
}


class TransactionPipeline:
    def __init__(
        self,
        parser_model: str = PARSER_MODEL,
        explainer_model: str = EXPLAINER_MODEL,
        use_llm_explainer: bool = True,
        verbose: bool = True,
        stream_delay: float = STREAM_DELAY
    ):
        """
        Initialize the transaction pipeline.

        Args:
            parser_model: Model for intent parsing
            explainer_model: Model for generating explanations
            use_llm_explainer: Whether to use LLM for explanations
            verbose: Whether to print debug info
            stream_delay: Delay in seconds between streaming chunks (0.0 = no delay)
                         Use 0.02-0.05 for readable typing effect
        """
        self.parser = IntentParser(model_name=parser_model)
        self.planner = QueryPlanner()
        self.executor = ToolExecutor()
        self.explainer = ResponseExplainer(model_name=explainer_model, stream_delay=stream_delay)
        self.audit = AuditLogger()
        self.use_llm_explainer = use_llm_explainer
        self.verbose = verbose
        self.active_customer_id: Optional[int] = None

    def _log(self, msg: str):
        if self.verbose:
            print(msg)

    def resolve_customer_id(self, intent: ParsedIntent):
        """Apply active customer fallback and update tracking.

        If the current query has a customer_id, remember it.
        If not, fall back to the last-used customer_id.
        """
        if intent.customer_id is not None:
            self.active_customer_id = intent.customer_id
        elif self.active_customer_id is not None:
            intent.customer_id = self.active_customer_id
            self._log(f"    [Session] Using active customer: {mask_customer_id(self.active_customer_id)}")

    def _should_get_insights(self, intent: ParsedIntent) -> bool:
        """Check if this intent benefits from transaction insights."""
        return intent.intent in INSIGHT_INTENTS and intent.customer_id is not None

    def query(self, user_query: str) -> PipelineResponse:
        start_time = time.time()

        self._log(f"\n{'='*60}")
        self._log(f"Query: {user_query}")
        self._log('='*60)

        # Phase 1: Parse intent
        self._log("\n[1] Parsing intent...")
        intent = self.parser.parse(user_query)
        self.resolve_customer_id(intent)
        self._log(f"    Intent: {intent.intent.value}")
        self._log(f"    Customer: {mask_customer_id(intent.customer_id) if intent.customer_id else 'N/A'}")
        if intent.category:
            self._log(f"    Category: {intent.category}")
        self._log(f"    Confidence: {intent.confidence}")

        # Phase 2: Create plan
        self._log("\n[2] Creating execution plan...")
        plan, error = self.planner.create_plan(intent)

        if error:
            self._log(f"    Error: {error}")
            return self._error_response(intent, error, start_time)

        self._log(f"    Plan: {[p['tool'] for p in plan]}")

        # Phase 3: Execute tools
        self._log("\n[3] Executing tools...")
        results = self.executor.execute(plan)

        for r in results:
            status = "OK" if r.success else f"FAIL: {r.error}"
            self._log(f"    {r.tool_name}: {status}")

        # Phase 3.5: Transaction insights (if needed)
        transaction_insights = None
        if self._should_get_insights(intent):
            self._log("\n[3.5] Extracting transaction insights...")
            transaction_insights = get_transaction_insights_if_needed(intent.customer_id)
            if transaction_insights:
                self._log(f"    Patterns found: {len(transaction_insights.patterns)}")
            else:
                self._log("    No patterns detected")

        # Phase 4: Generate explanation (streaming by default)
        self._log("\n[4] Generating response (streaming)...\n")
        if self.use_llm_explainer:
            answer = ""
            for chunk in self.explainer.stream_explain(intent, results, transaction_insights):
                print(chunk, end='', flush=True)
                answer += chunk
            print()  # Newline after streaming
        else:
            answer = self.explainer.format_simple(results)
            print(answer)

        # Build response
        response = PipelineResponse(
            answer=answer,
            data={r.tool_name: r.result for r in results if r.success},
            intent=intent,
            tools_used=[r.tool_name for r in results],
            success=True
        )

        # Phase 5: Audit log
        latency = (time.time() - start_time) * 1000
        self._log_audit(user_query, intent, results, answer, latency, True)

        return response

    def query_stream(self, user_query: str) -> Iterator[str]:
        """
        Stream the response as it's being generated.
        Yields chunks of the final answer as they are produced by the LLM explainer.
        """
        start_time = time.time()

        self._log(f"\n{'='*60}")
        self._log(f"Query: {user_query}")
        self._log('='*60)

        # Phase 1: Parse intent
        self._log("\n[1] Parsing intent...")
        intent = self.parser.parse(user_query)
        self.resolve_customer_id(intent)
        self._log(f"    Intent: {intent.intent.value}")
        self._log(f"    Customer: {mask_customer_id(intent.customer_id) if intent.customer_id else 'N/A'}")
        if intent.category:
            self._log(f"    Category: {intent.category}")
        self._log(f"    Confidence: {intent.confidence}")

        # Phase 2: Create plan
        self._log("\n[2] Creating execution plan...")
        plan, error = self.planner.create_plan(intent)

        if error:
            self._log(f"    Error: {error}")
            yield error
            self._log_audit(user_query, intent, [], error, (time.time() - start_time) * 1000, False, error)
            return

        self._log(f"    Plan: {[p['tool'] for p in plan]}")

        # Phase 3: Execute tools
        self._log("\n[3] Executing tools...")
        results = self.executor.execute(plan)

        for r in results:
            status = "OK" if r.success else f"FAIL: {r.error}"
            self._log(f"    {r.tool_name}: {status}")

        # Phase 3.5: Transaction insights (if needed)
        transaction_insights = None
        if self._should_get_insights(intent):
            self._log("\n[3.5] Extracting transaction insights...")
            transaction_insights = get_transaction_insights_if_needed(intent.customer_id)
            if transaction_insights:
                self._log(f"    Patterns found: {len(transaction_insights.patterns)}")
            else:
                self._log("    No patterns detected")

        # Phase 4: Stream explanation
        self._log("\n[4] Generating response (streaming)...\n")

        if self.use_llm_explainer:
            full_answer = ""
            for chunk in self.explainer.stream_explain(intent, results, transaction_insights):
                full_answer += chunk
                yield chunk
            answer = full_answer
        else:
            answer = self.explainer.format_simple(results)
            yield answer

        # Phase 5: Audit log
        latency = (time.time() - start_time) * 1000
        self._log_audit(user_query, intent, results, answer, latency, True)

    def _error_response(
        self,
        intent: ParsedIntent,
        error: str,
        start_time: float
    ) -> PipelineResponse:
        latency = (time.time() - start_time) * 1000
        self._log_audit(intent.raw_query, intent, [], error, latency, False, error)

        return PipelineResponse(
            answer=error,
            intent=intent,
            success=False,
            error=error
        )

    def _log_audit(
        self,
        query: str,
        intent: ParsedIntent,
        results: list,
        response: str,
        latency: float,
        success: bool,
        error: str = None
    ):
        audit = AuditLog(
            query=query,
            parsed_intent=intent,
            tools_executed=results,
            response=response,
            latency_ms=latency,
            success=success,
            error=error
        )
        self.audit.log(audit)
