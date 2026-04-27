#src/agentcy/llm_utilities/async_context_manager.py

from typing import Optional
from ollama import AsyncClient
from openai import OpenAI
import openai
import asyncio
import logging

class AsyncClientManager(AsyncClient):
    """
    A wrapper around the library's AsyncClient that adds support for
    'async with' usage by defining __aenter__ and __aexit__.
    """

    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_value, traceback):
        # Properly close the underlying httpx.AsyncClient on exit
        await self._client.aclose()

# openai_manager.py



class AsyncOpenAIClientManager:
    """
    A context manager that sets the OpenAI API key and wraps the synchronous
    openai.ChatCompletion.create call with run_in_executor so it can be used
    in async code without blocking the event loop.
    """

    def __init__(self, api_key: str, concurrency_limit: int = 10):
        self.api_key = api_key
        # If you want a local concurrency limit for this client:
        self.semaphore = asyncio.Semaphore(concurrency_limit)

    async def __aenter__(self):
        openai.api_key = self.api_key
        # If there's any other OpenAI global config, set it here.
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Nothing special to close for the official OpenAI library
        pass

    async def chat(self, model: str, messages: list, timeout: float = 30.0):
        """
        Asynchronously calls openai.ChatCompletion.create by offloading
        the synchronous call to a thread via run_in_executor.

        Returns the entire response dict from the API call, or None on error.
        """
        loop = asyncio.get_running_loop()
        client = OpenAI(api_key=self.api_key)
        # Acquire concurrency slot
        async with self.semaphore:
            try:
                def blocking_call():
                    # Use the ChatCompletion API instead of completions.create
                    return client.chat.completions.create(
                        model=model,
                        messages=messages,
                    )
                # Offload the synchronous call to a separate thread
                response = await loop.run_in_executor(None, blocking_call)
                return response
            except Exception as e:
                logging.error(f"OpenAI call failed: {e}", exc_info=True)
                return None