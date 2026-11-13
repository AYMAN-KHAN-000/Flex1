from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import types

from flex.discord.manager import DiscordManager, hash_password
from flex.security.secret_store import protect_secret, unprotect_secret


class FakeCommandTree:
    def __init__(self, _client):
        self.commands = {}

    def command(self, *, name, description):
        def register(callback):
            self.commands[name] = callback
            return callback

        return register

    async def sync(self):
        return []


class FakeModal:
    def __init_subclass__(cls, *, title=None, **kwargs):
        super().__init_subclass__(**kwargs)


def _install_fake_command_api(fake_discord):
    fake_discord.app_commands = types.SimpleNamespace(CommandTree=FakeCommandTree)
    fake_discord.ui = types.SimpleNamespace(
        Modal=FakeModal,
        TextInput=lambda **_kwargs: object(),
    )
    fake_discord.AllowedMentions = types.SimpleNamespace(none=lambda: None)


class FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False


class FakeChannel:
    def __init__(self):
        self.id = 0
        self.sent = []

    def typing(self):
        return FakeTyping()

    async def send(self, content, **_kwargs):
        self.sent.append(content)


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_discord_connect_reports_ready_only_after_gateway_ready(monkeypatch, tmp_path):
    allow_ready = threading.Event()
    clients = []

    class FakeClient:
        def __init__(self, *, intents):
            self.events = {}
            self.closed = False
            self.stopped = asyncio.Event()
            clients.append(self)

        def event(self, callback):
            self.events[callback.__name__] = callback
            return callback

        async def start(self, token, *, reconnect):
            await asyncio.to_thread(allow_ready.wait)
            await self.events["on_ready"]()
            await self.stopped.wait()

        async def close(self):
            self.closed = True
            self.stopped.set()

        def is_closed(self):
            return self.closed

    fake_discord = types.ModuleType("discord")
    fake_discord.Intents = types.SimpleNamespace(
        default=lambda: types.SimpleNamespace(message_content=False)
    )
    fake_discord.Client = FakeClient
    fake_discord.LoginFailure = type("LoginFailure", (Exception,), {})
    fake_discord.HTTPException = type("HTTPException", (Exception,), {})
    _install_fake_command_api(fake_discord)
    monkeypatch.setitem(sys.modules, "discord", fake_discord)

    manager = DiscordManager(tmp_path, token="test-token")
    assert manager.connect() == "Connecting"
    assert manager.status == "Connecting"
    assert not manager.connected
    allow_ready.set()
    assert _wait_for(lambda: manager.status == "Connected")
    assert manager.connected

    assert manager.disconnect() == "Disconnected"
    assert clients[0].closed


def test_discord_connect_does_not_report_invalid_token_as_connected(
    monkeypatch,
    tmp_path,
):
    class FakeClient:
        def __init__(self, *, intents):
            pass

        def event(self, callback):
            return callback

        async def start(self, token, *, reconnect):
            raise fake_discord.LoginFailure()

        async def close(self):
            pass

        def is_closed(self):
            return True

    fake_discord = types.ModuleType("discord")
    fake_discord.Intents = types.SimpleNamespace(
        default=lambda: types.SimpleNamespace(message_content=False)
    )
    fake_discord.Client = FakeClient
    fake_discord.LoginFailure = type("LoginFailure", (Exception,), {})
    fake_discord.HTTPException = type("HTTPException", (Exception,), {})
    _install_fake_command_api(fake_discord)
    monkeypatch.setitem(sys.modules, "discord", fake_discord)

    manager = DiscordManager(tmp_path, token="bad-token")
    manager.connect()
    assert _wait_for(lambda: manager._thread is not None and not manager._thread.is_alive())
    assert manager.status == "Invalid Token"
    assert not manager.connected


def test_activation_persists_user_and_rate_limits_failed_passwords(tmp_path):
    manager = DiscordManager(tmp_path)
    manager.activation_password = hash_password("correct horse")

    for _ in range(5):
        assert manager._try_activate(123, "test-user", "wrong") in {
            "incorrect",
            "blocked",
        }
    assert manager._try_activate(123, "test-user", "correct horse") == "blocked"
    assert not manager.is_active_user(123)

    manager._activation_blocks.clear()
    assert manager._try_activate(123, "test-user", "correct horse") == "activated"
    assert manager.is_active_user(123)
    assert manager.storage.list_users()[0].username == "test-user"

    assert manager.remove_user(123)
    assert manager.storage.was_revoked(123)


def test_discord_messages_separate_public_chat_from_pc_access(tmp_path):
    manager = DiscordManager(tmp_path, prefix="-flex")
    handled = []
    manager.set_request_handler(
        lambda user_id, context, request: handled.append(
            (user_id, context, request)
        )
        or "answer"
    )
    channel = FakeChannel()
    author = types.SimpleNamespace(id=456, bot=False)
    client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
    fake_discord = types.SimpleNamespace(
        AllowedMentions=types.SimpleNamespace(none=lambda: None)
    )

    guild_message = types.SimpleNamespace(
        id=1,
        author=author,
        guild=types.SimpleNamespace(id=10),
        channel=channel,
        content="-flex explain source code",
    )
    asyncio.run(manager._handle_message(guild_message, client, fake_discord))
    assert handled == [
        ("456", "guild:10:channel:0:user:456", "explain source code")
    ]
    assert channel.sent == ["answer"]

    pc_message = types.SimpleNamespace(
        id=2,
        author=author,
        guild=types.SimpleNamespace(id=10),
        channel=channel,
        content="-flex open chrome",
    )
    asyncio.run(manager._handle_message(pc_message, client, fake_discord))
    assert len(handled) == 1
    assert "activation" in channel.sent[-1].lower()

    manager.activate_user(456, "test-user")
    dm_message = types.SimpleNamespace(
        id=3,
        author=author,
        guild=None,
        channel=channel,
        content="what is running on my computer?",
    )
    asyncio.run(manager._handle_message(dm_message, client, fake_discord))
    assert handled[-1] == ("456", "dm:456", "what is running on my computer?")


def test_revoked_user_gets_pc_access_notice_and_public_ai_still_works(tmp_path):
    manager = DiscordManager(tmp_path)
    manager.activate_user(777, "removed-user")
    manager.remove_user(777)
    handled = []
    manager.set_request_handler(
        lambda user_id, context, request: handled.append(request) or "public answer"
    )
    channel = FakeChannel()
    author = types.SimpleNamespace(id=777, bot=False)
    client = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
    fake_discord = types.SimpleNamespace(
        AllowedMentions=types.SimpleNamespace(none=lambda: None)
    )
    message = types.SimpleNamespace(
        id=4,
        author=author,
        guild=None,
        channel=channel,
        content="open notepad",
    )
    asyncio.run(manager._handle_message(message, client, fake_discord))
    assert "revoked" in channel.sent[-1].lower()
    assert not handled

    message.id = 5
    message.content = "explain how photosynthesis works"
    asyncio.run(manager._handle_message(message, client, fake_discord))
    assert handled == ["explain how photosynthesis works"]
    assert channel.sent[-1] == "public answer"


def test_discord_response_chunks_respect_discord_length_limit():
    chunks = DiscordManager.split_message(("word " * 900).strip())
    assert len(chunks) > 1
    assert all(len(chunk) <= 2000 for chunk in chunks)


def test_pc_action_classifier_does_not_block_code_generation():
    manager = DiscordManager()
    assert manager.requires_pc_access("open chrome")
    assert manager.requires_pc_access("create a folder on my desktop")
    assert manager.requires_pc_access("what is currently running on my computer?")
    assert not manager.requires_pc_access("create a simple HTML game")
    assert not manager.requires_pc_access("explain how to open source code")


def test_real_discord_command_tree_registers_required_commands():
    import discord

    client = discord.Client(intents=discord.Intents.default())
    tree = discord.app_commands.CommandTree(client)
    manager = DiscordManager()
    manager.activation_password = hash_password("activation password")
    manager._register_commands(discord, tree)
    assert {command.name for command in tree.get_commands()} == {
        "guide",
        "flex-activate",
    }

    class FakeResponse:
        def __init__(self):
            self.modal = None
            self.messages = []

        async def send_modal(self, modal):
            self.modal = modal

        async def send_message(self, content, **kwargs):
            self.messages.append((content, kwargs))

    interaction = types.SimpleNamespace(
        user=types.SimpleNamespace(id=888, __str__=lambda self: "activation-user"),
        response=FakeResponse(),
    )
    activate_command = tree.get_command("flex-activate")
    asyncio.run(activate_command.callback(interaction))
    assert interaction.response.modal.title == "Activate FLEX PC Access"
    assert (
        interaction.response.modal.password.placeholder
        == "Enter the password configured by the FLEX owner"
    )

    guide_interaction = types.SimpleNamespace(
        user=types.SimpleNamespace(id=888),
        response=FakeResponse(),
    )
    asyncio.run(tree.get_command("guide").callback(guide_interaction))
    assert guide_interaction.response.messages[0][1]["ephemeral"] is True


def test_saved_discord_token_uses_windows_user_protection():
    if os.name != "nt":
        return
    token = "test-discord-token"
    protected = protect_secret(token)
    assert token not in protected
    assert protected.startswith("dpapi:v1:")
    assert unprotect_secret(protected) == token
