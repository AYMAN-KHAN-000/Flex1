# FLEX

### Your Personal AI Desktop Assistant.

FLEX is an AI-powered Windows desktop assistant designed to understand natural-language instructions, interact with your computer, automate workflows, browse the web, and assist with everyday tasks.

Built with Python, powered by configurable AI models, and designed around structured tools, local execution, security, and verification.

**Created by Ayman Khan.**

---

## Releases

**Latest Version:** `v0.1.0 Beta` *(planned initial release)*

FLEX is currently under active development. Follow the releases page for new versions, improvements, and updates.

[**Download Latest Release**](../../releases) · [**View All Releases**](../../releases) · [**Report an Issue**](../../issues)

> Beta releases may contain bugs, incomplete features, and breaking changes.

---

## Preview

<!-- IMAGE 1: Main FLEX application screenshot -->
![FLEX 1](https://i.postimg.cc/G3788wGW/15.png)

<!-- IMAGE 2: FLEX Notch screenshot -->
![FLEX Notch](https://i.postimg.cc/G3788wv0/16.png)

<!-- IMAGE 3: FLEX Discord integration screenshot -->
![FLEX features](https://i.postimg.cc/wxfyy8DH/17.png)

<!-- IMAGE 4: FLEX Browser / Research screenshot -->
![FLEX ](https://i.postimg.cc/VsGrrQn2/18.png)

<!-- IMAGE 5: FLEX Settings screenshot -->
![FLEX 3](https://i.postimg.cc/4NYKpLQr/19.png)

---

## What is FLEX?

FLEX is more than a chatbot. It is a desktop AI agent that translates user instructions into structured actions and executes them through registered tools.

Instead of allowing an AI model to directly execute arbitrary commands, FLEX uses a controlled architecture:

**User → AI Agent → Planner → Tool Registry → Security Validation → Execution → Verification**

This architecture allows FLEX to interact with Windows while maintaining local permission checks, controlled execution, and verifiable results.

## Features

### AI & Conversations
- Natural-language AI conversations.
- Configurable OpenRouter AI provider.
- Structured tool calling.
- Context-aware interactions.
- Markdown-formatted responses.

### Windows Control
- Open and close applications.
- Open websites and browser pages.
- Manage permitted user files.
- Windows Start menu and Search.
- Control volume and supported display brightness.
- Wi-Fi controls.
- System information and screenshots.
- Confirmed restart, shutdown, sleep, and other protected actions.

### FLEX Notch
- Minimal floating desktop overlay.
- Screen-edge hover activation.
- Text and voice interaction.
- Animated eyes and expressions.
- Sleep and wake states.
- Quick interaction without opening the main window.

### Voice Assistant
- On-device speech recognition using Faster-Whisper.
- Windows built-in text-to-speech.
- Voice conversations.
- Local microphone processing.
- Recognized speech follows the same AI response path as text.

### Discord Integration
- Chat with FLEX through Discord DMs and servers.
- Bot activation and user authorization.
- Individual PC-control permissions.
- Configurable server prefix.
- User access management.
- Protected actions require local confirmation.

### Browser & Smart Research
- Playwright browser automation.
- Web searches and page inspection.
- Search result ranking.
- Source citations.
- Product comparisons with price evidence.
- Browser navigation and interaction.
- Multi-step browser workflows.

### Memory & Personalization
- Persistent local structured memory.
- Saved preferences and project context.
- Quick folders and notes.
- Personal usage memory.
- Study folder indexing.
- Configurable memory controls.

### Automation & Tasks
- Multi-step AI workflows.
- Registered tool execution.
- Step-by-step verification.
- Task scheduling and notifications.
- Start and end reminders.
- Workflow cancellation and bounded retries.

### Windows Utilities
- Clipboard access with permission.
- Screenshot capture.
- Volume management.
- Display brightness management.
- Wi-Fi status and controls.
- Windows Settings navigation.

---

## How FLEX Works

FLEX follows a structured agent architecture.

| Component | Responsibility |
|---|---|
| AI Provider | Understands requests and proposes tool calls |
| Agent | Coordinates the interaction |
| Planner | Organizes multi-step tasks |
| Tool Registry | Provides available capabilities |
| Security Layer | Validates permissions and arguments |
| Executor | Performs approved actions |
| Verifier | Checks execution results |
| Memory | Stores supported local context |

The AI proposes actions, but FLEX controls their execution.

Protected actions require appropriate authorization, and successful results are verified before FLEX reports completion.

---

## Technology Stack

| Technology | Purpose |
|---|---|
| Python 3.13+ | Core application |
| PySide6 | Desktop interface |
| OpenRouter | AI provider |
| SQLite | Local memory and application data |
| Playwright | Browser automation |
| Faster-Whisper | Speech recognition |
| Windows TTS | Voice responses |
| PyInstaller | Windows executable packaging |
| Inno Setup | Windows installer |

---

## Installation

### Option 1 — Windows Installer

1. Visit the [FLEX Releases](../../releases) page.
2. Download the latest Windows setup file.
3. Run the installer.
4. Follow the installation instructions.
5. Launch FLEX.

*Installer availability depends on the published release.*

### Option 2 — Run from Source

**Requirements**
- Windows 10/11
- Python 3.13 or newer
- OpenRouter API key

Clone the repository:

```bash
git clone https://github.com/AYMAN-KHAN-000/FLEX.git
```

Enter the project directory:

```bash
cd FLEX
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Install the Playwright browser:

```bash
python -m playwright install chromium
```

Configure environment variables:

```powershell
copy .env.example .env
```

Add your OpenRouter API key to `.env`:

```env
OPENROUTER_API_KEY=your_key_here
OPENROUTER_MODEL=openai/gpt-4o-mini
```

Start FLEX:

```bash
python main.py
```

---

## Discord Setup

FLEX includes a Discord bot integration.

1. Create an application in the Discord Developer Portal.
2. Create a bot and copy its token.
3. Enable Message Content Intent.
4. Invite the bot with the `bot` and `applications.commands` scopes.
5. Open FLEX and navigate to Discord.
6. Enter the bot token and activation password.
7. Save the configuration.

FLEX supports individual user activation for computer-control access. Public users can use AI chat without receiving PC-control permissions.

See the project documentation for additional security and permission details.

---

## Security & Privacy

Security is an important part of FLEX's architecture.

- Structured tool execution instead of unrestricted AI command execution.
- Local argument validation.
- Permission-based tool access.
- Confirmation for protected actions.
- Filesystem restrictions.
- No unrestricted shell or PowerShell execution.
- Local SQLite memory.
- Credential-like data rejected from persistent memory.
- Discord PC-control authorization.
- Browser content treated as untrusted input.
- Execution results verified before reporting success.

FLEX does not automatically upload screenshots or microphone recordings for AI analysis. Recognized voice text and other relevant request context may be sent to the configured AI provider.

---

## Development Roadmap

FLEX is continuously evolving.

Areas of ongoing development include:

- Improving AI agent intelligence.
- Expanding Windows automation.
- Improving voice interaction.
- Enhancing the FLEX Notch.
- Expanding Discord capabilities.
- Improving workflow automation.
- Improving installation and update processes.
- Optimizing application performance.

Features and availability may change between releases.

---

## Contributing

Contributions, suggestions, and bug reports are welcome.

If you find a problem or have an idea:

1. Open an issue.
2. Describe the problem or suggestion.
3. Include relevant screenshots or error logs.
4. Submit a pull request if you have a proposed improvement.

Please avoid including API keys, passwords, or other private information in issues.

---

## Creator

**Ayman Khan**

Independent developer and creator of FLEX.

[Portfolio](https://aymanweb.vercel.app) · [Website](https://aymanbio.vercel.app) · [GitHub](https://github.com/AYMAN-KHAN-000)

---

<p align="center">

### FLEX

**Intelligence. Automation. Control.**

Built to bring AI closer to your desktop.

</p>

---

<p align="center">
  © Ayman Khan. All rights reserved.
</p>
