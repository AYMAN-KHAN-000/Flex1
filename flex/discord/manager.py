from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


logger = logging.getLogger(__name__)


def normalize_discord_user_id(value: str | int | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def hash_password(password: str) -> str:
    if not password:
        return ""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(password: str | None, password_hash: str | None) -> bool:
    if not password or not password_hash or not password_hash.startswith("pbkdf2_sha256$"):
        return False
    parts = password_hash.split("$", 2)
    if len(parts) != 3:
        return False
    _, salt, digest_hex = parts
    if not salt or not digest_hex:
        return False
    try:
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    if len(expected) != hashlib.sha256().digest_size:
        return False
    computed = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    )
    return secrets.compare_digest(computed, expected)


@dataclass
class DiscordUser:
    discord_id: str
    username: str = ""
    activation_date: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "Authorized"


class AuthorizationStore:
    def __init__(self, project_root: str | Path | None = None) -> None:
        root = Path(project_root) if project_root is not None else Path.cwd()
        self.storage_path = root / "data" / "discord_authorized_users.json"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._revoked_ids: set[str] = set()

    def load(self) -> list[DiscordUser]:
        with self._lock:
            if not self.storage_path.exists():
                self._revoked_ids = set()
                return []
            try:
                with self.storage_path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError, TypeError):
                logger.exception("Could not read the Discord authorization store.")
                return []
            if isinstance(payload, list):
                entries = payload
            elif isinstance(payload, dict):
                entries = payload.get("users", [])
            else:
                logger.error("Discord authorization store has an invalid format.")
                return []
            if not isinstance(entries, list):
                logger.error("Discord authorization store has an invalid users field.")
                return []
            revoked_ids = payload.get("revoked_ids", []) if isinstance(payload, dict) else []
            if not isinstance(revoked_ids, list):
                logger.error("Discord authorization store has an invalid revoked IDs field.")
                revoked_ids = []
            self._revoked_ids = {
                normalize_discord_user_id(discord_id)
                for discord_id in revoked_ids
                if normalize_discord_user_id(discord_id)
            }
            users: list[DiscordUser] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                discord_id = normalize_discord_user_id(entry.get("discord_id"))
                username = str(entry.get("username") or "")
                if not discord_id:
                    continue
                users.append(
                    DiscordUser(
                        discord_id=discord_id,
                        username=username,
                        activation_date=str(entry.get("activation_date") or datetime.now(timezone.utc).isoformat()),
                        status=str(entry.get("status") or "Authorized"),
                    )
                )
            return users

    def save(self, users: list[DiscordUser], *, revoked_ids: set[str] | None = None) -> None:
        with self._lock:
            if revoked_ids is not None:
                self._revoked_ids = set(revoked_ids)
            payload = {
                "users": [
                    {
                        "discord_id": user.discord_id,
                        "username": user.username,
                        "activation_date": user.activation_date,
                        "status": user.status,
                    }
                    for user in users
                ],
                "revoked_ids": sorted(self._revoked_ids),
            }
            temporary_path = self.storage_path.with_suffix(".json.tmp")
            try:
                with temporary_path.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                temporary_path.replace(self.storage_path)
            except OSError:
                logger.exception("Could not save the Discord authorization store.")
                raise

    def is_authorized(self, discord_user_id: str | int | None) -> bool:
        return any(user.discord_id == normalize_discord_user_id(discord_user_id) for user in self.load())

    def activate_user(self, discord_user_id: str | int, username: str = "") -> DiscordUser:
        user_id = normalize_discord_user_id(discord_user_id)
        with self._lock:
            users = self.load()
            existing = next((user for user in users if user.discord_id == user_id), None)
            if existing is not None:
                existing.username = username or existing.username
                existing.status = "Authorized"
                self._revoked_ids.discard(user_id)
                self.save(users)
                return existing
            user = DiscordUser(discord_id=user_id, username=username, status="Authorized")
            users.append(user)
            self._revoked_ids.discard(user_id)
            self.save(users)
            return user

    def remove_user(self, discord_user_id: str | int) -> bool:
        user_id = normalize_discord_user_id(discord_user_id)
        with self._lock:
            users = self.load()
            filtered = [user for user in users if user.discord_id != user_id]
            if len(filtered) == len(users):
                return False
            self._revoked_ids.add(user_id)
            self.save(filtered)
            return True

    def list_users(self) -> list[DiscordUser]:
        return self.load()

    def was_revoked(self, discord_user_id: str | int | None) -> bool:
        user_id = normalize_discord_user_id(discord_user_id)
        self.load()
        return user_id in self._revoked_ids

    def clear(self) -> None:
        self.save([], revoked_ids=set())


class DiscordManager:
    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        token: str = "",
        prefix: str = "-flex",
        status_callback: Callable[[str], None] | None = None,
        users_changed_callback: Callable[[], None] | None = None,
    ) -> None:
        root = Path(project_root) if project_root is not None else Path.cwd()
        self.project_root = root
        self.storage = AuthorizationStore(root)
        self.token = token.strip()
        self.prefix = (prefix or "-flex").strip() or "-flex"
        self.status = "Disconnected"
        self.connected = False
        self.client: Any | None = None
        self._status_callback = status_callback
        self._users_changed_callback = users_changed_callback
        self._thread: threading.Thread | None = None
        self._loop: Any | None = None
        self._tree: Any | None = None
        self._stopping = threading.Event()
        self._connection_lock = threading.Lock()
        self._activation_lock = threading.Lock()
        self._activation_failures: dict[str, deque[float]] = {}
        self._activation_blocks: dict[str, float] = {}
        self._request_handler: Callable[[str, str, str], str] | None = None
        self._seen_message_ids: set[int] = set()
        self._seen_message_order: deque[int] = deque(maxlen=4096)
        self._commands_synced = False
        self._activity_lock = threading.Lock()
        self._recent_activity: deque[str] = deque(maxlen=50)

    def set_request_handler(
        self,
        handler: Callable[[str, str, str], str] | None,
    ) -> None:
        self._request_handler = handler

    @property
    def recent_activity(self) -> list[str]:
        with self._activity_lock:
            return list(self._recent_activity)

    def _record_activity(self, user_id: str | int, source: str, outcome: str) -> None:
        entry = (
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | "
            f"Discord ID {normalize_discord_user_id(user_id)} | {source} | {outcome}"
        )
        with self._activity_lock:
            self._recent_activity.appendleft(entry)

    def _set_status(self, status: str) -> None:
        with self._connection_lock:
            self.status = status
            self.connected = status == "Connected"
        if self._status_callback is not None:
            try:
                self._status_callback(status)
            except RuntimeError:
                logger.warning("Discord status notification target is no longer available.")
                self._status_callback = None

    def configure(self, *, token: str | None = None, prefix: str | None = None, activation_password: str | None = None) -> dict[str, Any]:
        if token is not None:
            self.token = token.strip()
        if prefix is not None:
            self.prefix = (prefix or "-flex").strip() or "-flex"
        if activation_password is not None:
            self.activation_password = activation_password
        return {"token_present": bool(self.token), "prefix": self.prefix, "connected": self.connected}

    @property
    def activation_password(self) -> str:
        return getattr(self, "_activation_password", "")

    @activation_password.setter
    def activation_password(self, value: str) -> None:
        self._activation_password = value or ""

    def save_configuration(self, *, token: str | None = None, activation_password: str | None = None, prefix: str | None = None) -> dict[str, Any]:
        if token is not None:
            self.token = token.strip()
        if activation_password is not None:
            self.activation_password = activation_password
        if prefix is not None:
            self.prefix = (prefix or "-flex").strip() or "-flex"
        return {"status": self.status, "connected": self.connected, "prefix": self.prefix}

    def reset_configuration(self) -> None:
        if self.disconnect() != "Disconnected":
            raise RuntimeError("The Discord connection did not stop; configuration was not reset.")
        self.token = ""
        self.activation_password = ""
        self.prefix = "-flex"
        self.storage.clear()
        with self._activation_lock:
            self._activation_failures.clear()
            self._activation_blocks.clear()
        with self._activity_lock:
            self._recent_activity.clear()
        self._seen_message_ids.clear()
        self._seen_message_order.clear()
        if self._users_changed_callback is not None:
            self._users_changed_callback()

    def connect(self) -> str:
        if self._thread is not None and self._thread.is_alive():
            return self.status
        if not self.token:
            self._set_status("Invalid Token")
            return self.status
        self._stopping.clear()
        self._commands_synced = False
        self._set_status("Connecting")
        self._thread = threading.Thread(
            target=self._run_client,
            name="FLEX Discord client",
            daemon=True,
        )
        self._thread.start()
        return self.status

    def _run_client(self) -> None:
        if self._stopping.is_set():
            self._set_status("Disconnected")
            return
        try:
            import asyncio

            import discord
        except ImportError:
            logger.exception(
                "Discord integration dependency is unavailable. Install project requirements."
            )
            self._set_status("Connection Error")
            return

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        client = None
        try:
            intents = discord.Intents.default()
            intents.message_content = True
            client = discord.Client(intents=intents)
            self.client = client
            tree = discord.app_commands.CommandTree(client)
            self._tree = tree
            self._register_commands(discord, tree)

            @client.event
            async def on_ready() -> None:
                if not self._commands_synced:
                    try:
                        await tree.sync()
                        self._commands_synced = True
                    except discord.HTTPException:
                        logger.exception("Could not register FLEX Discord slash commands.")
                if not self._stopping.is_set():
                    self._set_status("Connected")

            @client.event
            async def on_disconnect() -> None:
                if not self._stopping.is_set():
                    self._set_status("Connecting")

            @client.event
            async def on_resumed() -> None:
                if not self._stopping.is_set():
                    self._set_status("Connected")

            @client.event
            async def on_message(message: Any) -> None:
                await self._handle_message(message, client, discord)

            if not self._stopping.is_set():
                loop.run_until_complete(client.start(self.token, reconnect=True))
        except discord.LoginFailure:
            logger.warning("Discord rejected the configured bot token.")
            self._set_status("Invalid Token")
        except discord.HTTPException:
            logger.exception("Discord connection failed with an HTTP error.")
            self._set_status("Connection Error")
        except Exception:
            if not self._stopping.is_set():
                logger.exception("Discord connection failed.")
                self._set_status("Connection Error")
        finally:
            try:
                if client is not None and not client.is_closed():
                    loop.run_until_complete(client.close())
            except Exception:
                if not self._stopping.is_set():
                    logger.exception("Could not close the Discord client cleanly.")
            finally:
                self.client = None
                self._tree = None
                self._loop = None
                loop.close()
                if self._stopping.is_set() or self.status == "Connected":
                    self._set_status("Disconnected")

    def disconnect(self) -> str:
        self._stopping.set()
        loop = self._loop
        client = self.client
        thread = self._thread
        if loop is not None and client is not None and not loop.is_closed():
            import asyncio

            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop).result(timeout=5)
            except (TimeoutError, RuntimeError):
                logger.warning("Timed out while closing the Discord client.")
            except Exception:
                logger.exception("Could not close the Discord client.")
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        if thread is not None and thread.is_alive():
            logger.error("Discord client thread did not stop before the timeout.")
            self._set_status("Connection Error")
            return self.status
        self.client = None
        self._tree = None
        self._thread = None
        self._loop = None
        self._set_status("Disconnected")
        return self.status

    def get_status(self) -> str:
        return self.status

    def is_active_user(self, discord_user_id: str | int | None) -> bool:
        return self.storage.is_authorized(discord_user_id)

    def activate_user(self, discord_user_id: str | int, username: str = "") -> DiscordUser:
        user = self.storage.activate_user(discord_user_id, username)
        if self._users_changed_callback is not None:
            self._users_changed_callback()
        return user

    def remove_user(self, discord_user_id: str | int) -> bool:
        removed = self.storage.remove_user(discord_user_id)
        if removed and self._users_changed_callback is not None:
            self._users_changed_callback()
        return removed

    def list_users(self) -> list[DiscordUser]:
        return self.storage.list_users()

    def verify_activation_password(self, candidate: str | None) -> bool:
        return verify_password(candidate, self.activation_password)

    def parse_prefix_command(self, message: str) -> tuple[bool, str]:
        text = (message or "").strip()
        if not text:
            return False, ""
        prefix = self.prefix
        if text.startswith(prefix):
            remainder = text[len(prefix):].lstrip()
            return True, remainder
        return False, ""

    def requires_pc_access(self, message: str) -> bool:
        return bool(
            re.match(
                r"^\s*(?:(?:please|can you|could you|would you)\s+)?"
                r"(?:open|launch|close|start|stop|shutdown|restart|sign out|"
                r"lock|sleep|wake|delete|move|copy|rename|"
                r"click|search my files|find my files|list running apps|"
                r"create\s+(?:a\s+|an\s+)?(?:file|folder|directory)|"
                r"write\s+(?:to|into)\s+(?:the\s+|a\s+)?(?:file|document)|"
                r"what(?:'s| is)(?: currently)? running|"
                r"what(?:'s| is) on my screen|check my (?:pc|computer|screen|system)|"
                r"control my (?:pc|computer)|take (?:a )?screenshot|turn (?:on|off))\b",
                message or "",
                re.IGNORECASE,
            )
        )

    @staticmethod
    def split_message(text: str, limit: int = 2000) -> list[str]:
        remaining = (text or "").strip()
        if not remaining:
            return [""]
        chunks: list[str] = []
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, limit + 1)
            if split_at < limit // 2:
                split_at = remaining.rfind(" ", 0, limit + 1)
            if split_at < limit // 2:
                split_at = limit
            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _register_commands(self, discord: Any, tree: Any) -> None:
        @tree.command(name="guide", description="Learn how to use FLEX in Discord.")
        async def guide(interaction: Any) -> None:
            self._record_activity(interaction.user.id, "slash command", "/guide")
            await interaction.response.send_message(
                (
                    "FLEX Discord guide\n\n"
                    f"Public AI: send a DM, or use `{self.prefix} your question` in a server.\n"
                    "Available commands: `/guide`, `/flex-activate`.\n"
                    "PC access: use `/flex-activate` and enter the activation password privately. "
                    "Only activated Discord accounts can request PC actions; protected actions "
                    "still require approval on the FLEX computer."
                ),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @tree.command(
            name="flex-activate",
            description="Request access to this FLEX computer.",
        )
        async def flex_activate(interaction: Any) -> None:
            user_id = normalize_discord_user_id(interaction.user.id)
            if self.is_active_user(user_id):
                self._record_activity(user_id, "slash command", "already activated")
                await interaction.response.send_message(
                    "Your FLEX PC access is already activated.",
                    ephemeral=True,
                )
                return
            if not self.activation_password:
                self._record_activity(user_id, "slash command", "activation unavailable")
                await interaction.response.send_message(
                    "PC activation is not configured in FLEX Settings.",
                    ephemeral=True,
                )
                return
            manager = self

            class ActivationModal(discord.ui.Modal, title="Activate FLEX PC Access"):
                password = discord.ui.TextInput(
                    label="Activation password",
                    placeholder="Enter the password configured by the FLEX owner",
                    required=True,
                    max_length=128,
                )

                async def on_submit(self, modal_interaction: Any) -> None:
                    try:
                        result = await asyncio.to_thread(
                            manager._try_activate,
                            modal_interaction.user.id,
                            str(modal_interaction.user),
                            str(self.password),
                        )
                    except OSError:
                        logger.exception("Could not persist Discord user activation.")
                        manager._record_activity(
                            modal_interaction.user.id,
                            "activation",
                            "authorization storage error",
                        )
                        await modal_interaction.response.send_message(
                            "FLEX could not save the activation. PC access was not granted; contact the FLEX owner.",
                            ephemeral=True,
                        )
                        return
                    if result == "activated":
                        manager._record_activity(
                            modal_interaction.user.id,
                            "activation",
                            "access activated",
                        )
                        response = (
                            "FLEX PC access activated successfully. You can now use FLEX "
                            "to interact with the connected computer."
                        )
                    elif result == "blocked":
                        manager._record_activity(
                            modal_interaction.user.id,
                            "activation",
                            "attempts temporarily blocked",
                        )
                        response = (
                            "Too many incorrect attempts. Please try again in 10 minutes."
                        )
                    else:
                        manager._record_activity(
                            modal_interaction.user.id,
                            "activation",
                            "incorrect password",
                        )
                        response = "Incorrect activation password. PC access has not been granted."
                    await modal_interaction.response.send_message(
                        response,
                        ephemeral=True,
                    )

            await interaction.response.send_modal(ActivationModal())

    def _try_activate(self, user_id: int, username: str, candidate: str) -> str:
        normalized_id = normalize_discord_user_id(user_id)
        now = time.monotonic()
        with self._activation_lock:
            blocked_until = self._activation_blocks.get(normalized_id, 0)
            if blocked_until > now:
                return "blocked"
            if self.verify_activation_password(candidate):
                self._activation_failures.pop(normalized_id, None)
                self._activation_blocks.pop(normalized_id, None)
                self.activate_user(normalized_id, username)
                return "activated"
            failures = self._activation_failures.setdefault(normalized_id, deque())
            failures.append(now)
            while failures and now - failures[0] > 600:
                failures.popleft()
            if len(failures) >= 5:
                self._activation_blocks[normalized_id] = now + 600
                self._activation_failures.pop(normalized_id, None)
                return "blocked"
        return "incorrect"

    async def _handle_message(self, message: Any, client: Any, discord: Any) -> None:
        if message.author.bot or (client.user and message.author.id == client.user.id):
            return
        message_id = int(message.id)
        if message_id in self._seen_message_ids:
            return
        self._seen_message_ids.add(message_id)
        if len(self._seen_message_order) == self._seen_message_order.maxlen:
            self._seen_message_ids.discard(self._seen_message_order[0])
        self._seen_message_order.append(message_id)

        is_dm = message.guild is None
        content = str(message.content or "").strip()
        if is_dm:
            used_prefix, request = self.parse_prefix_command(content)
            if not used_prefix:
                request = content
        else:
            used_prefix, request = self.parse_prefix_command(content)
            if not used_prefix:
                return
        if not request:
            return

        user_id = normalize_discord_user_id(message.author.id)
        authorized = self.is_active_user(user_id)
        if not authorized and self.requires_pc_access(request):
            self._record_activity(
                user_id,
                "DM" if is_dm else "server",
                "PC request denied (not activated)",
            )
            if self.storage.was_revoked(user_id):
                response = (
                    "Your FLEX PC access has been revoked. You can still use public AI features."
                )
            else:
                response = (
                    "PC control requires activation. Use `/flex-activate` to request access. "
                    "Public AI chat is still available."
                )
        elif self._request_handler is None:
            self._record_activity(
                user_id,
                "DM" if is_dm else "server",
                "request handling unavailable",
            )
            response = "FLEX Discord request handling is not configured. Check FLEX Settings."
        else:
            context_key = (
                f"dm:{user_id}"
                if is_dm
                else f"guild:{message.guild.id}:channel:{message.channel.id}:user:{user_id}"
            )
            try:
                async with message.channel.typing():
                    response = await asyncio.to_thread(
                        self._request_handler,
                        user_id,
                        context_key,
                        request,
                    )
                self._record_activity(
                    user_id,
                    "DM" if is_dm else "server",
                    "request handled",
                )
            except Exception:
                logger.exception("Could not process an incoming Discord request.")
                self._record_activity(
                    user_id,
                    "DM" if is_dm else "server",
                    "request failed",
                )
                response = "FLEX couldn't complete that request. Please try again."

        for chunk in self.split_message(str(response), 2000):
            await message.channel.send(
                chunk,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    def public_ai_help(self) -> str:
        return (
            "FLEX is ready. Public AI chat is available. Use the configured prefix, for example "
            f"`{self.prefix} explain quantum physics`. Activate your account with `/flex-activate` for PC control."
        )
