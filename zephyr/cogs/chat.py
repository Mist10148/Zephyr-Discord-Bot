"""Chat / AI slash commands: /settings, /output, /token, /prompt, /generate,
/image-gen.

Ported 1:1 from the original bot.py (chat slash commands 3027-3263, plus the
image-generation cooldown/cache block from 2477-2488 & 3165-3203). The actual
Gemini engine lives in ``zephyr.services.gemini``; the message-event handler
lives on the bot in ``zephyr.client``.
"""

import io
from collections import deque
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from google.genai import types

from zephyr.services.gemini import (
    gemini_async_client,
    get_context_settings,
    set_context_settings,
    save_user_settings,
    resolve_fallback_models,
    get_model_usage_snapshot,
    build_progress_bar,
    format_datetime_for_user,
    generate_gemini_response,
    send_response,
    MODEL_LIMITS,
)

# ---------------------------------------------------------------------------
# Image Generation Rate Limiting (original 2477-2488)
# ---------------------------------------------------------------------------
IMAGE_GEN_USER_COOLDOWN = 60  # seconds between requests per user
IMAGE_GEN_GUILD_COOLDOWN = 30  # seconds between requests per guild
IMAGE_GEN_CACHE_SIZE = 10
IMAGE_GEN_CACHE_TTL = 300  # seconds to keep cached images

image_gen_user_cooldowns = {}
image_gen_guild_cooldowns = {}
image_gen_cache = {}
image_gen_cache_order = deque()


def _check_image_gen_cooldown(user_id: int, guild_id):
    now = datetime.now(timezone.utc).timestamp()
    user_ready = image_gen_user_cooldowns.get(user_id, 0)
    guild_ready = image_gen_guild_cooldowns.get(guild_id, 0) if guild_id else 0
    user_remaining = max(0, user_ready - now)
    guild_remaining = max(0, guild_ready - now)
    if user_remaining > 0 or guild_remaining > 0:
        return False, max(user_remaining, guild_remaining)
    return True, 0


def _update_image_gen_cooldown(user_id: int, guild_id):
    now = datetime.now(timezone.utc).timestamp()
    image_gen_user_cooldowns[user_id] = now + IMAGE_GEN_USER_COOLDOWN
    if guild_id:
        image_gen_guild_cooldowns[guild_id] = now + IMAGE_GEN_GUILD_COOLDOWN


def _get_cached_image(prompt: str):
    now = datetime.now(timezone.utc).timestamp()
    entry = image_gen_cache.get(prompt)
    if entry and now - entry["timestamp"] <= IMAGE_GEN_CACHE_TTL:
        return entry
    return None


def _cache_image(prompt: str, files, text):
    now = datetime.now(timezone.utc).timestamp()
    # Evict oldest entries if over size
    while len(image_gen_cache_order) >= IMAGE_GEN_CACHE_SIZE:
        oldest = image_gen_cache_order.popleft()
        image_gen_cache.pop(oldest, None)
    # Store raw bytes so we can recreate discord.File objects later
    image_gen_cache[prompt] = {
        "timestamp": now,
        "files": files,
        "text": text,
    }
    image_gen_cache_order.append(prompt)


class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="settings", description="Customize AI model and response format for this server/DM.")
    @app_commands.describe(
        ai_model="Select the AI model to use for generating responses.",
        response_format="Choose how the bot's responses are formatted."
    )
    @app_commands.choices(
        ai_model=[
            app_commands.Choice(name="⚡ 3.1 Flash-Lite (Recommended)", value="gemini-3.1-flash-lite"),
            app_commands.Choice(name="⚡ 2.5 Flash-Lite", value="gemini-2.5-flash-lite"),
            app_commands.Choice(name="⚡ 2.5 Flash", value="gemini-2.5-flash"),
            app_commands.Choice(name="🧠 Pro (Powerful & Complex)", value="gemini-2.5-pro"),
        ],
        response_format=[
            app_commands.Choice(name="📄 Embed (Default)", value="embed"),
            app_commands.Choice(name="✍️ Normal Text", value="text"),
            app_commands.Choice(name="📁 Send as .txt file (for long responses)", value="txt"),
        ]
    )
    async def settings(self, interaction: discord.Interaction, ai_model: app_commands.Choice[str], response_format: app_commands.Choice[str]):
        server_id = interaction.guild.id if interaction.guild else None
        set_context_settings(
            server_id=server_id,
            user_id=interaction.user.id,
            settings={"ai_model": ai_model.value, "response_format": response_format.value},
        )
        save_user_settings()
        embed = discord.Embed(
            title="✅ Settings Updated!",
            description="Your preferences have been saved for this context (Server or DM).",
            color=discord.Color.green()
        )
        embed.add_field(name="AI Model", value=ai_model.name)
        embed.add_field(name="Response Format", value=response_format.name)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="output", description="Quickly switch the chatbot response format between embed and normal text.")
    @app_commands.describe(format="Choose embed or normal text")
    @app_commands.choices(
        format=[
            app_commands.Choice(name="📄 Embed", value="embed"),
            app_commands.Choice(name="✍️ Normal Text", value="text"),
        ]
    )
    async def output_command(self, interaction: discord.Interaction, format: app_commands.Choice[str]):
        """Quick command to toggle chatbot output format without changing the AI model."""
        server_id = interaction.guild.id if interaction.guild else None
        current = get_context_settings(server_id, interaction.user.id)
        current["response_format"] = format.value
        set_context_settings(server_id=server_id, user_id=interaction.user.id, settings=current)
        save_user_settings()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Output Format Updated",
                description=f"Responses will now be sent as **{format.name}**.",
                color=discord.Color.green()
            ),
            ephemeral=True
        )

    @app_commands.command(name="token", description="Show the current Gemini token and request usage for this session.")
    async def token(self, interaction: discord.Interaction):
        server_id = interaction.guild.id if interaction.guild else None
        settings = get_context_settings(server_id, interaction.user.id)
        effective_model = settings["ai_model"]
        fallback_models = resolve_fallback_models(effective_model)
        fallback_label = ", ".join(fallback_models) if fallback_models else "None"
        limits = MODEL_LIMITS.get(effective_model)
        if not limits:
            await interaction.response.send_message(f"No local quota tracker is configured for `{effective_model}`.", ephemeral=True)
            return
        snapshot = await get_model_usage_snapshot(effective_model)
        embed = discord.Embed(
            title="📊 Gemini Session Usage",
            description="This tracker is local to this bot process and resets when the bot restarts.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Effective Model", value=effective_model, inline=False)
        embed.add_field(name="Fallback Chain", value=fallback_label, inline=False)
        embed.add_field(name="RPM", value=f"{build_progress_bar(snapshot['rpm'], limits['rpm'])}\n{snapshot['rpm']} / {limits['rpm']}", inline=False)
        embed.add_field(name="TPM", value=f"{build_progress_bar(snapshot['tpm'], limits['tpm'])}\n{snapshot['tpm']:,} / {limits['tpm']:,}", inline=False)
        embed.add_field(name="RPD", value=f"{build_progress_bar(snapshot['rpd'], limits['rpd'])}\n{snapshot['rpd']} / {limits['rpd']}", inline=False)
        embed.add_field(
            name="Session Totals",
            value=(
                f"Prompt tokens: {snapshot['totals']['prompt_tokens']:,}\n"
                f"Output tokens: {snapshot['totals']['output_tokens']:,}\n"
                f"Total tokens: {snapshot['totals']['total_tokens']:,}\n"
                f"Successful requests: {snapshot['totals']['successful_requests']}\n"
                f"Outgoing requests: {snapshot['totals']['session_requests']}"
            ),
            inline=False,
        )
        cooldown_value = "No active cooldown"
        if snapshot["cooldown_until"]:
            cooldown_value = format_datetime_for_user(snapshot["cooldown_until"])
        embed.add_field(name="Cooldown Status", value=cooldown_value, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="prompt", description="Ask Gemini a question (supports text and images).")
    @app_commands.describe(message="Your question for the AI.", attachment="Attach an image or .txt file.")
    async def prompt(self, interaction: discord.Interaction, message: str = "", attachment: discord.Attachment = None):
        await interaction.response.defer()
        server_id = interaction.guild.id if interaction.guild else None
        user_id = interaction.user.id
        image_url, text_content = None, None
        if attachment:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                image_url = attachment.url
            elif attachment.filename.endswith(".txt"):
                text_content = (await attachment.read()).decode("utf-8")
        final_message = text_content or message
        if not final_message and not image_url:
            await interaction.followup.send("Please provide a message, an image, or a text file.", ephemeral=True)
            return
        response = await generate_gemini_response(server_id, user_id, final_message, image_url)
        await send_response(interaction.followup, response, interaction)

    @app_commands.command(name="generate", description="Generate an image")
    async def generate(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.defer()
        # image_generator module may not exist; handle gracefully
        try:
            import image_generator
            image_file, text_output = image_generator.generate_image(prompt)
        except Exception as e:
            await interaction.followup.send(f"Image generation is unavailable: {e}")
            return
        files = []
        if image_file:
            files.append(discord.File(image_file))
        content = f"**Generated Text Output:**\n{text_output}" if text_output else None
        await interaction.followup.send(content=content, files=files or discord.utils.MISSING)

    @app_commands.command(name="image-gen", description="Generate an image from a description using Gemini.")
    @app_commands.describe(prompt="Description of the image you want to generate")
    async def image_gen(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.defer()
        user_id = interaction.user.id
        guild_id = interaction.guild.id if interaction.guild else None

        allowed, remaining = _check_image_gen_cooldown(user_id, guild_id)
        if not allowed:
            await interaction.followup.send(
                f"⏳ Image generation is on cooldown. Please wait **{int(remaining)}** second(s) before trying again.",
                ephemeral=True
            )
            return

        cached = _get_cached_image(prompt)
        if cached:
            files = [discord.File(io.BytesIO(data), filename=name) for name, data in cached["files"]]
            await interaction.followup.send(content=cached["text"] or None, files=files)
            _update_image_gen_cooldown(user_id, guild_id)
            return

        try:
            response = await gemini_async_client.models.generate_content(
                model="gemini-3.1-flash-image-preview",
                contents=[types.UserContent(parts=[types.Part.from_text(text=prompt)])],
                config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
            )
        except Exception as e:
            await interaction.followup.send(f"Image generation failed: {e}")
            return

        image_bytes_list = []
        text_parts = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            if not content:
                continue
            for part in getattr(content, "parts", []) or []:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "data", None):
                    data = inline_data.data
                    mime = getattr(inline_data, "mime_type", "image/png")
                    ext = mime.split("/")[-1] or "png"
                    image_bytes_list.append((f"generated.{ext}", data))
                elif getattr(part, "text", None):
                    text_parts.append(part.text)

        if not image_bytes_list:
            await interaction.followup.send("No image was generated. Please try a different prompt.")
            return

        text_content = "\n".join(text_parts).strip() if text_parts else None
        files = [discord.File(io.BytesIO(data), filename=name) for name, data in image_bytes_list]
        await interaction.followup.send(content=text_content, files=files)

        _update_image_gen_cooldown(user_id, guild_id)
        _cache_image(prompt, image_bytes_list, text_content)


async def setup(bot: commands.Bot):
    await bot.add_cog(ChatCog(bot))
