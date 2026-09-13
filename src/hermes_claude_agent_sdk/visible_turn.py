"""Durable commentary acknowledgment before an MCP tool may enter Hermes.

No native request admission is claimed here: this boundary owns visible
transcript ordering and tool effects, not hidden model generations.
"""

import asyncio
import json

from agent.runtime_api import RuntimeAssistantUpdate


class VisibleTurn:
    def __init__(self, host, *, timeout=10.0):
        self.host = host
        self.timeout = timeout
        self.messages = {}
        self.current = None
        self.permits = []
        self.ready = asyncio.Condition()
        self.failure = None

    async def _seal(self):
        if self.current is None:
            return
        state = self.messages[self.current]
        if not state["sealed"]:
            await self.host.persist_assistant(RuntimeAssistantUpdate(
                self.current, state["sequence"], "", "final"))
            state["sequence"] += 1
            state["sealed"] = True
        self.current = None

    async def observe(self, projection):
        """Called synchronously in ordered SDK projection, never a display queue."""
        message_id = projection.assistant_message_id
        text = projection.final_text
        streamed = ""
        if message_id and text is not None:
            if self.current != message_id:
                await self._seal()
            state = self.messages.setdefault(message_id,
                {"sequence": 0, "text": None, "sealed": False})
            if state["sealed"] and state["text"] != text:
                raise ValueError("sealed assistant snapshot changed")
            if state["text"] != text:
                previous = state["text"] or ""
                streamed = text[len(previous):] if text.startswith(previous) else text
                # The first bounded update resets this identity's snapshot;
                # later chunks append without clipping the visible message.
                for offset in range(0, max(1, len(text)), 4000):
                    await self.host.persist_assistant(RuntimeAssistantUpdate(
                        message_id, state["sequence"], text[offset:offset + 4000],
                        "snapshot" if offset == 0 else "delta"))
                    state["sequence"] += 1
                state["text"] = text
            self.current = message_id
        if projection.tool_bindings or projection.is_result:
            await self._seal()
        # A permit is published only AFTER all preceding commentary is durable.
        # A later MCP callback consumes the exact native call identity/payload.
        async with self.ready:
            self.permits.extend(projection.tool_bindings)
            self.ready.notify_all()
        return streamed

    async def admit_tool(self, name, arguments):
        key = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        qualified = "mcp__hermes-tools__" + name
        async def wait():
            async with self.ready:
                while True:
                    for index, (request_id, observed_name, observed_args) in enumerate(self.permits):
                        if observed_name == qualified and json.dumps(
                            observed_args, sort_keys=True, ensure_ascii=False,
                        ) == key:
                            self.permits.pop(index)
                            return request_id
                    if self.host.cancellation_requested():
                        raise RuntimeError("tool cancelled before transcript acknowledgment")
                    await self.ready.wait()
        try:
            return await asyncio.wait_for(wait(), self.timeout)
        except Exception:
            self.failure = "tool_transcript_acknowledgment_missing"
            raise

    async def finish(self):
        # Graceful cancellation/error seals already observed commentary only.
        await self._seal()
