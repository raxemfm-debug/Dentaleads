"""
Abstract provider interfaces — the extensibility boundary between business logic and
external services. All integrations must implement these interfaces; business logic
must never call external SDKs directly.

See docs/adr/001-provider-abstraction.md for the rationale.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Messaging (WhatsApp today; BSPs or other channels tomorrow)
# ---------------------------------------------------------------------------

@dataclass
class InboundMessage:
    tenant_phone_id: str   # identifies the clinic (tenant)
    from_number: str
    message_id: str
    text: str | None
    message_type: str      # text | button | list_reply | image | ...
    raw_payload: dict


@dataclass
class OutboundMessage:
    to_number: str
    text: str | None = None
    template_name: str | None = None
    template_params: list[str] | None = None
    interactive: dict | None = None


class MessagingProvider(ABC):
    """Send and receive messages over any supported channel."""

    @abstractmethod
    async def send_message(self, message: OutboundMessage) -> str:
        """Send a message; return the provider message ID."""

    @abstractmethod
    async def send_template(
        self,
        to_number: str,
        template_name: str,
        params: list[str],
    ) -> str:
        """Send an approved template (for messages outside the 24h session window)."""

    @abstractmethod
    def parse_inbound(self, raw_payload: dict) -> list[InboundMessage]:
        """Parse a raw webhook payload into a list of inbound messages."""

    @abstractmethod
    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        """Validate the webhook signature to authenticate the provider."""


# ---------------------------------------------------------------------------
# LLM (Claude today; any other model tomorrow)
# ---------------------------------------------------------------------------

@dataclass
class LLMTool:
    name: str
    description: str
    input_schema: dict


@dataclass
class LLMMessage:
    role: str   # user | assistant | system | tool_result
    content: str
    tool_call_id: str | None = None  # required when role == "tool_result"


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict]   # [{name, inputs}] when the model invokes tools
    stop_reason: str
    usage: dict              # {input_tokens, output_tokens}


class LLMProvider(ABC):
    """Generate text and invoke tools via any supported large language model."""

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[LLMTool] | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Run a completion; return the model response."""

    @abstractmethod
    async def classify(
        self,
        text: str,
        categories: list[str],
        system_prompt: str | None = None,
    ) -> str:
        """Cheap single-turn classification. Implementations may use a smaller model."""
