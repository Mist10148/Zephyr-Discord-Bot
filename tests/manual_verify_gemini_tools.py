"""Manual verification script for Gemini multi-tool combinations.

Run this against the live Gemini API to confirm the four built-in tools
(search, code execution, maps grounding, URL context) can be registered and
invoked together on a Gemini 3.5 model. This costs API credits, so it is not
part of the automated pytest suite.

Usage:
    .venv/Scripts/python tests/manual_verify_gemini_tools.py

Required: GEMINI_API_KEY in .env or in the environment.
"""

import asyncio
import os
from unittest.mock import MagicMock

from dotenv import load_dotenv

load_dotenv()

from zephyr.services.gemini import (
    gemini_async_client,
    get_generate_config,
    extract_grounding_sources,
    extract_maps_sources,
    extract_url_context_pages,
    extract_code_executions,
    detect_fired_tools,
    build_response_embed,
)


PROMPTS = [
    ("code", "Calculate the factorial of 15 exactly."),
    ("maps", "Find highly-rated restaurants near Times Square, New York."),
    ("url", "Summarize the content of https://en.wikipedia.org/wiki/Gemini_(language_model)"),
    ("search", "What are the latest headlines about artificial intelligence?"),
    ("combined", "Search for the current weather in Tokyo and also summarize https://example.com — wait, just search for the weather and summarize https://en.wikipedia.org/wiki/Tokyo"),
    ("none", "Tell me a short joke about programming."),
]


def _fake_author():
    author = MagicMock()
    author.display_name = "Tester"
    author.display_avatar.url = "https://example.com/avatar.png"
    return author


async def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Set GEMINI_API_KEY in .env or the environment.")
        return

    model = "gemini-3.5-flash"
    system_personality = (
        "You are a helpful assistant.\n\n"
        "Use tools when they help answer the user."
    )
    config = get_generate_config(system_personality)

    print(f"Model: {model}")
    print(f"Tools registered: {len(config.tools)}")
    for tool in config.tools:
        print(f"  - {tool}")
    print("-" * 60)

    for expected_tool, prompt in PROMPTS:
        print(f"\n[Prompt] {prompt}")
        try:
            response = await gemini_async_client.models.generate_content(
                model=model,
                contents=[{"role": "user", "parts": [{"text": prompt}]}],
                config=config,
            )
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        bot_response = response.text or "(no text)"
        fired = detect_fired_tools(response)
        print(f"  Fired tools: {fired}")

        web_sources, _ = extract_grounding_sources(response)
        maps_sources = extract_maps_sources(response)
        url_pages = extract_url_context_pages(response)
        code_executions = extract_code_executions(response)

        if expected_tool == "code" and not code_executions:
            print("  ⚠️ Expected code execution but none detected.")
        if expected_tool == "maps" and not maps_sources:
            print("  ⚠️ Expected maps grounding but none detected.")
        if expected_tool == "url" and not url_pages:
            print("  ⚠️ Expected URL context but none detected.")
        if expected_tool == "search" and not web_sources:
            print("  ⚠️ Expected web search but none detected.")
        if expected_tool == "combined" and not (web_sources and url_pages):
            print("  ⚠️ Expected combined search + URL context.")
        if expected_tool == "none" and fired:
            print(f"  ⚠️ Expected no tools but {fired} fired.")

        embed = build_response_embed(
            bot_response=bot_response,
            code_executions=code_executions,
            web_sources=web_sources,
            maps_sources=maps_sources,
            url_pages=url_pages,
            author=_fake_author(),
        )
        print(f"  Description: {embed.description[:200]}...")
        for field in embed.fields:
            print(f"  Field '{field.name}': {field.value[:200]}...")


if __name__ == "__main__":
    asyncio.run(main())
